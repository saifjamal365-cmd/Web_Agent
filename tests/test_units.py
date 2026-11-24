"""Fast tests: safety rules, tool schemas, storage, memory. No browser, no model."""
import uuid

import pytest

from webagent.agent import memory, safety
from webagent.agent import tools as T
from webagent.llm import parse_json
from webagent.store import db


def el(name, role="button", **kw):
    return {"name": name, "role": role, **kw}


@pytest.mark.parametrize("name", ["Buy now", "Place order", "Proceed to checkout", "Send message", "Delete account",
                                  "Confirm payment", "Subscribe", "Pay 20 JOD", "Accept all", "Post"])
def test_risky_buttons_need_approval(name):
    assert safety.risky_reason(el(name)) is not None


@pytest.mark.parametrize("name", ["Search", "Next", "Show more", "Reject", "Add to cart", "Filters", "Sign in",
                                  "Aero Laptop 14", "Close"])
def test_safe_buttons_do_not(name):
    assert safety.risky_reason(el(name)) is None


def test_links_only_flagged_for_dangerous_words():
    assert safety.risky_reason(el("Orders history", role="link")) is None
    assert safety.risky_reason(el("Contact us", role="link")) is None
    assert safety.risky_reason(el("Delete my account", role="link")) is not None


def test_submit_of_unnamed_form_button_is_risky():
    assert safety.risky_reason(el("Go", role="button", type="submit", form="checkout-form")) is None  # "go" is safe word
    assert safety.risky_reason(el("Continue to step 2", role="button", type="submit", form="/payment")) is not None


@pytest.mark.parametrize("field", [
    el("Password", role="input:password", type="password"),
    el("Card number", role="input:text", autocomplete="cc-number"),
    el("CVV", role="input:text"),
    el("Code", role="input:text", field="otp_code"),
])
def test_secret_fields_are_refused(field):
    assert safety.secret_field_reason(field) is not None


def test_normal_fields_are_allowed():
    assert safety.secret_field_reason(el("Your name", role="input:text", field="name name")) is None
    assert safety.secret_field_reason(el("Search products", role="input:search")) is None


def test_card_number_text_is_detected():
    assert safety.looks_secret("4111 1111 1111 1111")
    assert not safety.looks_secret("MacBook Air M3")


def test_enter_in_search_is_fine_but_in_a_form_needs_approval():
    assert safety.submit_reason(el("Search products", role="input:search", form="search.html")) is None
    assert safety.submit_reason(el("Message", role="textarea", form="contact-send contact")) is not None


def test_domains(monkeypatch):
    from types import SimpleNamespace
    assert safety.domain_of("https://www.Amazon.com/x") == "amazon.com"
    assert safety.domain_reason("file:///etc/passwd")
    assert safety.domain_reason("https://example.com") is None  # no lists set
    monkeypatch.setattr(safety, "settings", SimpleNamespace(allowed_domains=["python.org"], blocked_domains=["evil.com"]))
    assert safety.domain_reason("https://docs.python.org/3/") is None
    assert "not on the allowed" in safety.domain_reason("https://example.com")
    assert "blocked" in safety.domain_reason("https://evil.com")


def test_tool_schemas_are_valid_for_groq():
    schemas = T.schemas()
    names = {s["function"]["name"] for s in schemas}
    assert {"go_to", "click", "type", "finish", "ask_user", "look", "note"} <= names
    for s in schemas:
        params = s["function"]["parameters"]
        assert params["type"] == "object"
        assert "why" in params["properties"]
        assert s["function"]["description"]


def test_validate_gives_clear_errors():
    with pytest.raises(ValueError, match="no tool named"):
        T.validate("hack", {})
    with pytest.raises(ValueError, match="Wrong arguments for click"):
        T.validate("click", {"why": "x"})
    assert T.validate("scroll", {}).direction == "down"


def test_parse_json_handles_fences():
    assert parse_json('```json\n{"ok": true}\n```') == {"ok": True}
    assert parse_json('Sure! {"plan": ["a"]} done') == {"plan": ["a"]}
    assert parse_json("nothing") == {}


def test_task_lifecycle_in_db():
    t = db.create_task("Find a price", None, None)
    assert t["status"] == "queued"
    db.add_step(t["id"], node="act", tool="go_to", args={"url": "x"}, why="w", result="r", url="https://a.com")
    db.update_task(t["id"], status="done", answer="42")
    got = db.get_task(t["id"])
    assert got["status"] == "done" and got["steps_count"] == 1 and got["finished_at"]
    assert got["current_url"] == "https://a.com"
    assert db.get_steps(t["id"])[0]["tool"] == "go_to"


def test_interactions():
    t = db.create_task("Buy it", None, None)
    iid = db.open_interaction(t["id"], "approval", "Buy now?", {"what": "Click Buy"})
    assert db.pending_interaction(t["id"])["id"] == iid
    assert db.interaction_answer(iid) is None
    assert not db.answer_interaction(t["id"], {"text": "x"}, kind="question")
    assert db.answer_interaction(t["id"], {"approved": True}, kind="approval")
    assert db.interaction_answer(iid) == {"approved": True}
    assert db.pending_interaction(t["id"]) is None


def test_memory_finds_lessons_by_site_and_by_words():
    memory.remember("shop.example", "The search box is at the top; prices show under each product title.")
    memory.remember("news.example", "Articles have a summary box at the start.")
    by_site = memory.recall("anything", ["shop.example"])
    assert by_site and by_site[0]["domain"] == "shop.example"
    by_words = memory.recall("find product prices with the search box", [])
    assert any(s["domain"] == "shop.example" for s in by_words)
    # a near-duplicate replaces the old lesson
    memory.remember("shop.example", "The search box is at the top; prices show under each product title!")
    assert len([s for s in db.list_skills() if s["domain"] == "shop.example"]) == 1


def test_conversation_history():
    conv = uuid.uuid4().hex[:12]  # unique: a Postgres test database keeps rows between runs
    a = db.create_task("Q1", conv, None)
    db.update_task(a["id"], status="done", answer="A1")
    b = db.create_task("Q2", conv, None)
    hist = db.conversation_history(conv, b["id"])
    assert [h["answer"] for h in hist] == ["A1"]


def test_find_passages_returns_matching_paragraphs_in_page_order():
    from webagent.browser.session import find_passages
    text = "\n".join(["Intro line", "History of the country.", "It was admitted to the United Nations in 1955.",
                      "Unrelated text.", "Geography: the highest point is 1,854 m.", "More text."])
    got = find_passages(text, "admitted United Nations")
    assert "admitted to the United Nations in 1955" in got and "highest point" not in got
    assert got.index("History") < got.index("admitted")  # one line of context, page order
    assert find_passages(text, "zebra") == "" and find_passages(text, "a") == ""


def test_waiting_for_a_person_does_not_use_the_time_limit():
    from datetime import datetime, timezone

    from webagent.agent.graph import _pause_out
    from webagent.runner import _epoch
    assert _pause_out({"started": 100.0}, {"approved": True, "waited": 600}) == {"started": 700.0}
    assert _pause_out({"started": 100.0}, {"approved": True}) == {}
    naive_utc = datetime(2026, 11, 22, 0, 0, 0).isoformat()  # how SQLite returns it
    aware = datetime(2026, 11, 22, 0, 0, 0, tzinfo=timezone.utc).isoformat()  # how PostgreSQL returns it
    assert _epoch(naive_utc) == _epoch(aware)


def _step(tool, args, result="Selected 'iPhone 15'.", url="https://shop.test/compare", ok=True):
    return {"n": 0, "tool": tool, "args": args, "why": "", "result": result, "ok": ok, "url": url}


def test_third_identical_action_is_refused():
    from webagent.agent.graph import _repeat_reason
    pick = {"ref": "e1", "option": "iPhone 15"}
    url = "https://shop.test/compare"
    two = {"steps": [_step("select", pick), _step("select", pick)]}
    # the 3rd identical select on the same page is refused (the "why" text does not matter)
    assert _repeat_reason(two, "select", {**pick, "why": "again"}, url)
    # a reflect in between does not reset it: the action still cannot help
    assert _repeat_reason({"steps": two["steps"] + [_step("reflect", {})]}, "select", pick, url)
    # once is fine, another option is fine, another page is fine
    assert _repeat_reason({"steps": two["steps"][:1]}, "select", pick, url) is None
    assert _repeat_reason(two, "select", {"ref": "e1", "option": "iPhone 18"}, url) is None
    assert _repeat_reason(two, "select", pick, "https://shop.test/other") is None
    # different results (for example "Load more" showing new text) mean progress: allowed
    moving = {"steps": [_step("click", {"ref": "e9"}, "New text: items 1-10"),
                        _step("click", {"ref": "e9"}, "New text: items 11-20")]}
    assert _repeat_reason(moving, "click", {"ref": "e9"}, url) is None
    # scrolling down several times is normal
    scrolls = {"steps": [_step("scroll", {"direction": "down"}, "Scrolled down.")] * 3}
    assert _repeat_reason(scrolls, "scroll", {"direction": "down"}, url) is None
    # refused attempts do not count as tries
    refused = {"steps": [_step("select", pick), _step("select", pick, "Refused: ...", ok=False)]}
    assert _repeat_reason(refused, "select", pick, url) is None


def test_stuck_means_repeating_results_not_just_the_same_page():
    from webagent.agent.graph import _stuck
    scrolling = [_step("scroll", {"direction": "down"}, "Scrolled down.") for _ in range(8)]
    assert _stuck({"steps": scrolling})
    # 8 calculator buttons in a desktop app: same "url", but the display changes every time
    presses = [_step("desktop_click", {"ref": f"d{i}"}, f"Clicked.\nDisplay is {i}", url="") for i in range(8)]
    assert not _stuck({"steps": presses})


def test_model_gets_todays_date_and_trusts_the_page():
    import time

    from webagent.agent import prompts
    from webagent.agent.graph import _render
    text = _render({"task": "The iphone comparison", "plan": ["Compare"], "steps": []}, "(page)")
    assert f"TODAY: {time.strftime('%d %B %Y')}" in text and "trust the page" in text
    assert "newer" in prompts.SYSTEM and "newer" in prompts.REFLECT and "newer" in prompts.CHECK


def test_last_steps_are_for_answering_not_browsing():
    from webagent.agent import tools as tools_mod
    from webagent.agent.graph import FINAL_TOOLS, _final_stretch, _render
    assert not _final_stretch({"step_count": 30}, 40)
    assert _final_stretch({"step_count": 35}, 40)
    names = {s["function"]["name"] for s in tools_mod.schemas(FINAL_TOOLS)}
    assert names == {"note", "finish", "give_up"} and len(tools_mod.schemas()) == len(tools_mod.TOOLS)
    late = _render({"task": "t", "plan": [], "steps": [], "step_count": 36}, "(page)", 40)
    early = _render({"task": "t", "plan": [], "steps": [], "step_count": 10}, "(page)", 40)
    assert "FINAL STEPS" in late and "FINAL STEPS" not in early


# ---------------------------------------------------------------- tool groups (fewer tokens per turn)

def test_simple_task_gets_only_the_core_tools():
    assert T.task_groups("Latest stable Python version on python.org and its release date") == set()
    core = T.available(groups=set())
    assert "more_tools" in core and "read_table" not in core and "desktop_open" not in core
    assert len(core) < len(T.available())


def test_task_words_switch_on_groups():
    assert {"forms", "files"} <= T.task_groups("Fill the job application form and attach my CV")
    assert "tables" in T.task_groups("Compare the specs of the two phones")
    assert "login" in T.task_groups("Check my orders on the shop")
    assert "desktop" in T.task_groups("Use the calculator to work out 1234*56")


def test_the_page_switches_on_groups():
    from webagent.browser.session import Snapshot
    refs = {f"e{i}": {"role": r} for i, r in enumerate(["input:text", "input:email", "select", "input:password"])}
    refs["e9"] = {"role": "link", "href": "/docs/guide.pdf"}
    snap = Snapshot(url="http://x", title="", text="", refs=refs, tables=1)
    assert T.page_groups(snap) == {"tables", "forms", "files", "login"}
    assert T.page_groups(Snapshot(url="http://x", title="", text="")) == set()


def test_more_tools_adds_a_group_for_the_rest_of_the_task():
    args = T.validate("more_tools", {"groups": ["tables", "files"]})
    from types import SimpleNamespace
    res = T.run("more_tools", args, browser=SimpleNamespace(live_source=None), llm=None, snap=None)
    assert res.ok and set(res.groups) == {"tables", "files"} and "read_table" in res.text
    with pytest.raises(ValueError):
        T.validate("more_tools", {"groups": ["phone"]})


def test_more_tools_offers_only_the_groups_not_yet_on():
    more = next(s for s in T.schemas({"more_tools"}, missing=["tables", "login"]))["function"]
    assert more["parameters"]["properties"]["groups"]["items"]["enum"] == ["tables", "login"]
    assert "read_table" in more["description"] and "fill_form" not in more["description"]
    assert T.schemas({"more_tools"}, missing=[]) == []
    assert "helpers" not in T.missing_groups(set(), helper=True)


def test_tool_outside_the_offered_list_is_refused_with_a_hint():
    from webagent.agent import graph
    ctx = graph.Ctx(task_id="t-groups", browser=None, llm=None)
    graph.CONTEXTS["t-groups"] = ctx
    try:
        state = {"pending": {"name": "list_files", "args": {}, "url": "", "tools": sorted(T.available(groups=set()))},
                 "steps": []}
        out = graph.gate(state, {"configurable": {"thread_id": "t-groups"}})
        assert "more_tools" in out["decision"]["refused"] and "files" in out["decision"]["refused"]
        # harmless groups (tables, forms, gestures) need no extra more_tools step
        state["pending"] = {**state["pending"], "name": "read_table"}
        out = graph.gate(state, {"configurable": {"thread_id": "t-groups"}})
        assert "not available" not in str(out["decision"].get("refused", ""))
    finally:
        graph.CONTEXTS.pop("t-groups", None)


def test_login_window_opens_on_the_page_the_agent_is_on():
    from webagent.agent.graph import _login_url
    signin = "https://accounts.google.com/v3/signin/identifier?continue=https://chat.google.com/"
    assert _login_url("google.com", signin) == signin
    assert _login_url("github.com", signin) == "https://github.com"
    assert _login_url("https://github.com/login", "") == "https://github.com/login"


def test_login_is_only_saved_after_a_real_sign_in():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("login_script", Path(__file__).parent.parent / "scripts" / "login.py")
    login = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(login)
    anon = {"cookies": [{"name": "NID", "domain": ".google.com", "httpOnly": True}]}
    before = {("NID", ".google.com")}
    # Only looked at the page: no new cookies -> not signed in (this was the false "saved" for google.com)
    assert not login.signed_in(before, anon, "google.com", saw_password=False, password_now=False)
    after = {"cookies": anon["cookies"] + [{"name": "SID", "domain": ".google.com", "httpOnly": True}]}
    assert login.signed_in(before, after, "google.com", saw_password=True, password_now=False)
    # Still on the password form -> not signed in
    assert not login.signed_in(before, after, "google.com", saw_password=True, password_now=True)
    # A script cookie after a password form (the test shop) counts; a script cookie alone does not
    shop = {"cookies": [{"name": "shop_session", "domain": "127.0.0.1", "httpOnly": False}]}
    assert login.signed_in(set(), shop, "127.0.0.1", saw_password=True, password_now=False)
    assert not login.signed_in(set(), shop, "127.0.0.1", saw_password=False, password_now=False)


def test_agent_edge_mode_asks_to_sign_in_in_that_window(monkeypatch):
    import dataclasses
    from webagent import config
    from webagent.agent import tools
    on = dataclasses.replace(config.settings, agent_edge=True)
    monkeypatch.setattr(tools, "settings", on)
    q = tools.login_question("google.com")
    assert "agent's Edge" in q and "done" in q and "password" in q
    monkeypatch.setattr(tools, "settings", dataclasses.replace(config.settings, agent_edge=False))
    assert "Open login window" in tools.login_question("google.com")


def test_opening_the_same_address_a_third_time_is_refused():
    from webagent.agent.graph import _repeat_reason
    a = "https://docs.google.com/forms/d/e/1FAIpQ/viewform"
    steps = [{"tool": "go_to", "args": {"url": a}, "ok": True, "result": "Opened " + a},
             {"tool": "back", "args": {}, "ok": True, "result": "Went back"},
             {"tool": "go_to", "args": {"url": a + "/"}, "ok": True, "result": "Opened " + a},
             {"tool": "back", "args": {}, "ok": True, "result": "Went back"}]
    assert "twice" in _repeat_reason({"steps": steps}, "go_to", {"url": a}, "")
    assert _repeat_reason({"steps": steps[:2]}, "go_to", {"url": a}, "") is None


def test_the_same_click_is_not_a_loop_while_each_click_brings_new_text():
    from webagent.agent.graph import _stuck
    show_more = [{"tool": "click", "args": {"ref": "e1"}, "url": "u", "ok": True,
                  "result": f"New text on the page: review {i}"} for i in range(3)]
    assert not _stuck({"steps": show_more})          # "Show more" loading the next reviews each time
    same = [{"tool": "click", "args": {"ref": "e1"}, "url": "u", "ok": True,
             "result": "No visible text changed on the page."} for _ in range(3)]
    assert _stuck({"steps": same})


def test_done_so_far_keeps_real_actions_from_all_steps():
    from webagent.agent.graph import _done_so_far, _render
    steps = [{"n": 10, "tool": "click", "ok": True,
              "result": 'New text on the page: Cart (1) | Added to Cart: Lenovo IdeaPad Slim 5 14" Laptop'}]
    steps += [{"n": 11 + i, "tool": "find_in_page", "ok": True, "result": "Paragraphs ..."} for i in range(30)]
    state = {"task": "add the best laptop to the cart", "steps": steps}
    done = _done_so_far(state)
    assert done == ['step 10: Added to Cart: Lenovo IdeaPad Slim 5 14" Laptop']   # step 10 is long out of the recent list
    assert "DONE SO FAR" in _render(state, "") and "Slim 5" in _render(state, "")
    refused = {"task": "t", "steps": [{"n": 1, "tool": "click", "ok": False, "result": "Added to Cart: X"}]}
    assert _done_so_far(refused) == []


def test_a_malformed_tool_call_is_rebuilt_from_what_the_model_wrote():
    from webagent.llm import recover_tool_call
    names = {"find_in_page", "click", "finish"}
    c = recover_tool_call('{"name": "find_in_page", "arguments": {"words": "Select all"}}', names)
    assert c.name == "find_in_page" and c.args == {"words": "Select all"}
    c = recover_tool_call('<|channel|>commentary to=functions.click <|constrain|>json<|message|>{"ref": "e22"}', names)
    assert c.name == "click" and c.args == {"ref": "e22"}
    c = recover_tool_call('{"name": "click", "arguments": "{\\"ref\\": \\"e5\\"}"}', names)
    assert c.args == {"ref": "e5"}
    assert recover_tool_call('{"name": "delete_everything", "arguments": {}}', names) is None   # unknown tool
    assert recover_tool_call("I will click the button", names) is None


def test_groq_bad_tool_call_is_recovered_or_retried_only_once(monkeypatch):
    import webagent.llm as L
    monkeypatch.setattr(L.time, "sleep", lambda s: None)

    class BadCall(Exception):
        status_code = 400

        def __init__(self, gen):
            super().__init__("Error code: 400 - tool_use_failed")
            self.body = {"error": {"code": "tool_use_failed", "failed_generation": gen}}

    class Completions:
        calls = 0
        gen = ""

        def create(self, **kw):
            Completions.calls += 1
            raise BadCall(Completions.gen)

    llm = L.GroqLLM(api_key="test-key")
    llm.client = type("C", (), {"chat": type("Ch", (), {"completions": Completions()})()})()
    tools = [{"type": "function", "function": {"name": "finish", "parameters": {}}}]
    Completions.gen = '{"name": "finish", "arguments": {"answer": "done"}}'
    reply = llm.chat([{"role": "user", "content": "x"}], tools=tools)
    assert reply.tool_calls[0].name == "finish" and Completions.calls == 1
    Completions.calls, Completions.gen = 0, "gibberish"
    with pytest.raises(L.LLMError, match="invalid tool call"):
        llm.chat([{"role": "user", "content": "x"}], tools=tools)
    assert Completions.calls == 2                    # not 5 backed-off attempts


def test_planner_and_checker_do_not_add_requirements_the_person_did_not_state():
    from webagent.agent import prompts
    # Live run: "u should tell from where the important emails just" was read as "today's emails"; the checker then
    # rejected a correct list of senders and the agent searched for today's mail until the time ran out.
    assert 'never add a requirement they did not state' in prompts.PLAN and '"just"' in prompts.PLAN
    assert "Never add a requirement the person's words do not state" in prompts.CHECK
    assert "1-50 of\n  many" in prompts.SYSTEM or "of many" in prompts.SYSTEM


def test_a_question_the_person_answered_is_not_asked_again():
    """Live run (Google Chat): the agent drafted a reply, asked for approval, got "yes", and asked again 6 times."""
    from webagent.agent.graph import _asked_before, _render
    q = ('The last message from Mahmoud Ayman Sayed is: "I have other tasks..." A fast reply could be: "Sure, I will '
         'start improving the project today." Do you approve sending this reply?')
    state = {"task": "recommend a fast reply and get my approval to send it", "plan": [],
             "steps": [{**_step("ask_user", {"question": q}, "The person answered: yes approved"), "n": 4}]}
    again = _asked_before(state, q.replace("A fast reply could be", "Suggested quick reply"))
    assert again and "step 4" in again and "yes approved" in again and "type exactly that text" in again
    assert _asked_before(state, "Which of your two email accounts should I use?") is None
    # the model sees the whole question next to the answer, not 160 characters of it
    text = _render(state, "(page)")
    assert "Do you approve sending this reply?" in text and "THE PERSON ALREADY ANSWERED" in text


def test_text_the_person_approved_is_sent_without_asking_twice():
    from webagent.agent import tools as T
    from webagent.agent.graph import _approved_in_chat
    from webagent.browser.session import Snapshot
    q = 'Suggested reply: "Sure, I will start improving the project today." Do you approve sending it?'
    typed = _step("type", {"ref": "e5", "text": "Sure, I will start improving the project today."}, "Typed.")
    snap = Snapshot(url="https://chat.test", title="", text="", refs={
        "e7": {"ref": "e7", "role": "button", "name": "Send message"},
        "e8": {"ref": "e8", "role": "button", "name": "Delete conversation"}})
    yes = {"steps": [{**_step("ask_user", {"question": q}, "The person answered: yes"), "n": 3}, typed]}
    enter = T.validate("press", {"key": "Enter"})
    assert _approved_in_chat(yes, "press", enter) == (3, "yes")
    assert _approved_in_chat(yes, "click", T.validate("click", {"ref": "e7"}), snap)
    # only the action that sends it: never another risky button
    assert _approved_in_chat(yes, "click", T.validate("click", {"ref": "e8"}), snap) is None
    # other text, or a "no", still asks the person
    other = {"steps": [yes["steps"][0], _step("type", {"ref": "e5", "text": "I quit."}, "Typed.")]}
    assert _approved_in_chat(other, "press", enter) is None
    no = {"steps": [{**_step("ask_user", {"question": q}, "The person answered: no, make it shorter"), "n": 3}, typed]}
    assert _approved_in_chat(no, "press", enter) is None


def test_enter_in_a_chat_box_without_a_form_needs_approval():
    assert safety.submit_reason(el("Message Mahmoud Ayman Sayed", role="textbox")) is not None
    assert safety.submit_reason(el("History is on", role="textbox")) is not None
    assert safety.submit_reason(el("Search chat", role="input:text")) is None
    assert safety.submit_reason(el("Quantity", role="input:text")) is None


def test_send_shortcuts_and_enter_in_message_boxes_need_approval():
    """Review 24 Nov: keys "{Ctrl+Enter}" in Gmail and "Hello{Enter}" in a chat box sent without approval."""
    from webagent.browser.session import Snapshot
    refs = {"e5": {"ref": "e5", "role": "textbox", "name": "Message Body"},           # Gmail compose body
            "e6": {"ref": "e6", "role": "textbox", "name": "History is on"},          # Google Chat box
            "e7": {"ref": "e7", "role": "textbox", "name": "Document body"},          # a document editor
            "e8": {"ref": "e8", "role": "input:search", "name": "Search mail"},
            "e9": {"ref": "e9", "role": "canvas", "name": "drawing 900x500"}}         # a spreadsheet grid
    snap = Snapshot(url="https://mail.test", title="", text="", refs={k: {**v} for k, v in refs.items()})

    def kind(text, ref="", last=None):
        return T.gate("keys", T.validate("keys", {"text": text, "ref": ref}), snap, last).kind

    # send shortcuts, with or without a target
    for text in ("{Ctrl+Enter}", "{Control+Enter}", "{Cmd+Enter}", "{Meta+Return}", "Thanks!{Ctrl+Enter}", "{Alt+S}"):
        assert kind(text, "e5") == "approval", text
        assert kind(text) == "approval", text
    # Enter where Enter sends: the given box, or the box typed into last when no ref is given
    for text in ("Hello{Enter}", "Hello\n", "{Enter}", "{Return}"):
        assert kind(text, "e6") == "approval", text
        assert kind(text, last=refs["e6"]) == "approval", text
    # Enter that only starts a new line or searches: no approval
    assert kind("Line one\nLine two", "e5") == "ok"           # Gmail body: Enter is a new line
    assert kind("Monday: plan\nTuesday: review", "e7") == "ok"
    assert kind("invoice{Enter}", "e8") == "ok"
    assert kind("Date\tProduct\nMon\tMilk\n", "e9") == "ok"   # typing a table into a sheet
    assert kind("Date\tProduct\nMon\tMilk\n") == "ok"
    assert kind("{Ctrl+A}{Backspace}", "e6") == "ok"


def test_text_approved_in_chat_can_be_sent_with_keys():
    from webagent.agent.graph import _approved_in_chat
    q = 'Suggested reply: "Good morning, Eng. Mahmoud!" Do you approve sending it?'
    asked = {**_step("ask_user", {"question": q}, "The person answered: yes approved"), "n": 2}
    state = {"steps": [asked]}
    assert _approved_in_chat(state, "keys", T.validate("keys", {"text": "Good morning, Eng. Mahmoud!{Enter}"}))
    typed = _step("keys", {"text": "Good morning, Eng. Mahmoud!"}, "Typed.")
    assert _approved_in_chat({"steps": [asked, typed]}, "keys", T.validate("keys", {"text": "{Ctrl+Enter}"}))
    # other words are not covered by the yes
    assert _approved_in_chat(state, "keys", T.validate("keys", {"text": "See you never{Enter}"})) is None


# ---------------------------------------------------------------- fixes 1-3 (24 Nov) and Arabic

def _ex(url, more=True):
    res = "From " + url + ":\n- Aero 14: $650" + ("\nThis list goes on: the page has a Next / Show more control." if more else "")
    return _step("extract", {"what": "laptops: name, price"}, res, url=url)


def test_whole_list_answer_from_page_one_is_sent_back():
    from webagent.agent.graph import _code_check
    page1 = "https://shop.test/search?q=laptop"
    state = {"task": "Find the cheapest laptop", "answer": "The cheapest laptop is Aero 14 at $650.",
             "steps": [_ex(page1)]}
    problem, extra = _code_check(state)
    assert problem and problem["fix"] == "work" and "goes on" in problem["problem"] and extra == {}
    # opened page 2 of the same list: fine
    state["steps"] = [_ex(page1), _ex("https://shop.test/search?q=laptop&page=2", more=False)]
    assert _code_check(state) == ({}, {})
    # "Show more" loaded more rows on the same page: fine
    state["steps"] = [_ex(page1), _step("click", {"ref": "e9"}, "New text on the page: Zen 13 $590", url=page1)]
    assert _code_check(state) == ({}, {})
    # the answer limits itself to what was seen: fine
    state["steps"] = [_ex(page1)]
    state["answer"] = "Of the first 20 results, the cheapest is Aero 14 at $650."
    assert _code_check(state) == ({}, {})
    # a task that does not need the whole list: fine
    assert _code_check({"task": "Open the Aero 14 page", "answer": "Opened it: $650.", "steps": [_ex(page1)]}) == ({}, {})


def test_arabic_whole_list_task_is_checked_too():
    from webagent.agent.graph import _code_check
    state = {"task": "ابحث عن أرخص لابتوب", "answer": "أرخص لابتوب هو Aero 14 بسعر 650$.",
             "steps": [_ex("https://shop.test/search?q=laptop")]}
    assert _code_check(state)[0]


def test_nothing_new_gets_one_second_look():
    from webagent.agent.graph import _code_check
    for task, answer in [("Did Ahmad send me a new message?", "No, Ahmad has not sent any new message."),
                         ("Any new emails?", "There are no new emails."),
                         ("هل أرسل لي أحمد رسالة جديدة؟", "لا، لم يرسل أحمد أي رسالة جديدة."),
                         ("هل يوجد جديد في البريد؟", "لا جديد في البريد.")]:
        problem, extra = _code_check({"task": task, "answer": answer, "steps": []})
        assert problem and extra == {"second_look": True}, task
        # after the second look the answer goes on to the normal check
        assert _code_check({"task": task, "answer": answer, "steps": [], "second_look": True}) == ({}, {})
    # an action the person said no to is not a reading of the page
    st = {"task": "Reply to Ahmad's message", "answer": "I did not send the reply because you said no.",
          "steps": [], "rejected": [{"key": "x"}]}
    assert _code_check(st) == ({}, {})
    # a normal answer is not touched
    assert _code_check({"task": "Any new emails?", "answer": "Yes: 2 new emails, the newest from Sara at 10:02.",
                        "steps": []}) == ({}, {})


def test_extract_hint_is_not_given_for_a_previous_page_arrow():
    import inspect
    src = inspect.getsource(T)
    assert "[›»>]|التالي" in src


@pytest.mark.parametrize("name", ["إرسال", "ارسال", "اشتر الآن", "إتمام الطلب", "تأكيد الطلب", "حذف الحساب", "ادفع",
                                  "نشر", "اشترك", "احجز الآن"])
def test_arabic_risky_buttons_need_approval(name):
    assert safety.risky_reason(el(name)) is not None


@pytest.mark.parametrize("name", ["بحث", "التالي", "عرض المزيد", "رفض", "تسجيل الدخول", "إغلاق", "أضف إلى السلة",
                                  "لابتوب لينوفو"])
def test_arabic_safe_buttons_do_not(name):
    assert safety.risky_reason(el(name)) is None


def test_arabic_secret_fields_search_and_chat_boxes():
    assert safety.secret_field_reason(el("كلمة المرور", role="textbox"))
    assert safety.secret_field_reason(el("رقم البطاقة", role="textbox"))
    assert safety.secret_field_reason(el("رمز التحقق", role="textbox"))
    assert safety.secret_field_reason(el("الاسم الكامل", role="textbox")) is None
    assert safety.submit_reason(el("ابحث في المتجر", role="textbox", form="f")) is None
    assert safety.submit_reason(el("اكتب رسالة", role="input:text"))
    assert safety.risky_reason(el("حذف", role="link"))


def test_arabic_yes_and_no_answers():
    from webagent.agent.graph import _is_yes
    for a in ["نعم", "نعم أرسلها", "موافق", "أرسلها", "تمام", "حسنا", "اكيد", "yes"]:
        assert _is_yes(a), a
    for a in ["لا", "لا تبعث", "نعم لكن غيّر التحية", "انتظر", "no"]:
        assert not _is_yes(a), a


def test_asking_for_login_details_in_arabic_is_refused():
    assert T.ASKS_FOR_LOGIN.search("ما هي كلمة المرور الخاصة بك؟")
    assert T.ASKS_FOR_LOGIN.search("أرسل لي رمز التحقق")
    assert T.ASKS_FOR_LOGIN.search("ما هو البريد الإلكتروني لحساب أمازون؟")
    assert not T.ASKS_FOR_LOGIN.search("هل تريد اللون الأسود أم الأبيض؟")


def test_no_results_in_arabic():
    from webagent.browser.session import BrowserSession
    assert BrowserSession.NO_RESULTS.search("لا توجد نتائج لـ لابتوب")
    assert BrowserSession.NO_RESULTS.search("لم يتم العثور على أي منتج")


def test_memory_embeds_arabic_and_matches_spellings():
    v = memory.embed("ابحث عن أرخص لابتوب في المتجر")
    assert any(v)
    a, b = memory.embed("الأسعار في صفحة المنتج"), memory.embed("اسعار صفحة منتج")
    assert sum(x * y for x, y in zip(a, b)) > 0.5


def test_checker_that_fails_twice_never_passes_the_answer_as_checked(monkeypatch):
    from types import SimpleNamespace
    from webagent.agent import graph as G
    calls = []
    monkeypatch.setattr(G, "_chat_json", lambda *a, **k: calls.append(1) or {})

    def snap():
        raise RuntimeError("no page")
    ctx = SimpleNamespace(on_step=lambda s: None, browser=SimpleNamespace(url="https://shop.test/", snapshot=snap))
    monkeypatch.setitem(G.CONTEXTS, "t-check", ctx)
    out = G.check({"task": "Price of Aero 14?", "answer": "$650", "steps": [], "notes": []},
                  {"configurable": {"thread_id": "t-check"}})
    assert len(calls) == 2                                   # tried once more
    assert out["status"] == "done" and "could not be checked" in out["answer"]
    assert out["steps"][-1]["result"] == "Could not run the check."


# ---------------------------------------------------------------- the agent keeps what it read (24 Nov)

def _n(step, n):
    return {**step, "n": n}


def test_every_list_read_stays_in_full_for_the_next_turns():
    from webagent.agent.graph import _render
    p1, p2 = "https://shop.test/s?q=laptop", "https://shop.test/s?q=laptop&page=2"
    page1 = "From " + p1 + ":\n" + "\n".join(f"| Laptop {i} | ${500 + i} | 16 GB |" for i in range(30)) + \
            "\nThis list goes on: the page has a Next / Show more control."
    page2 = "From " + p2 + ":\n| ThinkPad T14 | $629.00 | 16 GB |"
    steps = [_n(_step("extract", {"question": "every laptop: name, price, RAM"}, page1, url=p1), 1),
             _n(_step("click", {"ref": "e9"}, "Now at " + p2, url=p2), 2),
             _n(_step("extract", {"question": "every laptop: name, price, RAM"}, page2, url=p2), 3)]
    steps += [_n(_step("scroll", {"direction": "down"}, "Scrolled.", url=p2), n) for n in range(4, 20)]
    text = _render({"task": "cheapest laptop with 16 GB", "steps": steps, "current_url": p2}, "(page)")
    assert "PAGES YOU ALREADY READ" in text
    assert "Laptop 29 | $529" in text                     # page 1 is still there, 18 steps later
    assert "ThinkPad T14 | $629.00" in text
    assert "the list goes on" in text and "the end of this list" in text


def test_reading_the_same_unchanged_page_again_is_refused():
    from webagent.agent.graph import _read_before
    p1, p2 = "https://shop.test/s", "https://shop.test/s?page=2"
    q = "every laptop: name, price, RAM"
    ex = _n(_step("extract", {"question": q}, "From x:\n| A | $1 |", url=p1), 1)
    nav = [_n(_step("click", {"ref": "e9"}, "Now at " + p2, url=p2), 2),
           _n(_step("back", {}, "Went back to " + p1, url=p1), 3)]
    assert _read_before({"steps": [ex] + nav}, p1, q) == 1
    assert _read_before({"steps": [ex] + nav}, p1, "every phone: name, price") is None    # another question
    assert _read_before({"steps": [ex] + nav}, p2, q) is None                            # another page
    # a filter on that page changed it: reading again is fine
    flt = _n(_step("click", {"ref": "e4"}, "e4 \"16 GB\" is now checked.", url=p1), 4)
    assert _read_before({"steps": [ex] + nav + [flt]}, p1, q) is None
    # the first read found nothing (still loading): fine
    empty = _n(_step("extract", {"question": q}, "From x:\nNothing found.", url=p1), 1)
    assert _read_before({"steps": [empty]}, p1, q) is None


def test_page_two_read_before_page_one_counts_as_the_whole_list():
    from webagent.agent.graph import _code_check
    p1, p2 = "https://shop.test/s?ram=16&page=1", "https://shop.test/s?ram=16&page=2"
    state = {"task": "the cheapest laptop", "answer": "The cheapest is T14 at $629.",
             "steps": [_ex(p2, more=False), _ex(p1, more=True)]}
    assert _code_check(state) == ({}, {})


def test_checker_claim_of_an_action_no_step_shows_is_ignored():
    from webagent.agent.graph import _claims_unseen_action
    read = [_n(_step("go_to", {"url": "u"}, "Opened u"), 1),
            _n(_step("extract", {"question": "laptops"}, "| T14 | $629 | Add to Cart |"), 2),
            _n(_step("click", {"ref": "e9"}, "Now at https://shop.test/s?page=2"), 3)]
    claim = "The agent added an item to the cart (step 2), which the task forbids."
    assert _claims_unseen_action({"steps": read}, claim)
    # a real add leaves a result: the claim stands
    added = read + [_n(_step("click", {"ref": "e5"}, "New text on the page: Added to Cart: T14"), 4)]
    assert not _claims_unseen_action({"steps": added}, claim)
    # an unknown click result: do not overrule the checker
    other = read + [_n(_step("click", {"ref": "e5"}, "New text on the page: Cart (1)"), 4)]
    assert not _claims_unseen_action({"steps": other}, claim)
    # other problems are not touched
    assert not _claims_unseen_action({"steps": read}, "The answer does not give the price.")


def test_json_mode_refusal_is_recovered_or_asked_again():
    from types import SimpleNamespace
    from webagent.llm import GroqLLM

    class Refused(Exception):
        status_code = 400

        def __init__(self, gen):
            super().__init__("json_validate_failed")
            self.body = {"error": {"code": "json_validate_failed", "failed_generation": gen}}

    def ok(text):
        msg = SimpleNamespace(content=text, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=SimpleNamespace(total_tokens=5))

    def make(gen, then):
        seen = []

        def create(**kw):
            seen.append("response_format" in kw)
            if "response_format" in kw:
                raise Refused(gen)
            return ok(then)
        llm = GroqLLM.__new__(GroqLLM)
        llm.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        llm.model, llm.retries = "openai/gpt-oss-120b", 2
        return llm, seen

    # the refused text is JSON after all (wrapped in words): used as it is, no second call
    llm, seen = make('Here: {"ok": true}', "unused")
    assert parse_json(llm.chat([], json_mode=True).text) == {"ok": True} and seen == [True]
    # not JSON: asked once more without JSON mode
    llm, seen = make("**Cheapest:** T14 $629", '```json\n{"ok": false, "fix": "answer"}\n```')
    assert parse_json(llm.chat([], json_mode=True).text)["ok"] is False and seen == [True, False]


def test_going_back_and_forth_between_two_pages_is_stopped():
    from webagent.agent.graph import _repeat_reason
    p1, p2 = "https://shop.test/s?page=1", "https://shop.test/s?page=2"
    hops = [_n(_step("click", {"ref": "e178" if i % 2 == 0 else "e174"}, "Now at " + (p2 if i % 2 == 0 else p1),
                     url=p2 if i % 2 == 0 else p1), i + 1) for i in range(4)]
    r = _repeat_reason({"steps": hops}, "click", {"ref": "e178"}, p1)
    assert r and "back and forth" in r
    assert _repeat_reason({"steps": hops}, "extract", {"question": "laptops"}, p1) is None    # reading is fine
    # three pages in a row (page 1, 2, 3, 4) is normal paging, not ping-pong
    walk = [_n(_step("click", {"ref": "e178"}, f"Now at p{i}", url=f"https://shop.test/s?page={i}"), i)
            for i in range(1, 5)]
    assert _repeat_reason({"steps": walk}, "click", {"ref": "e178"}, "https://shop.test/s?page=4") is None
    # a read in between breaks the loop
    mixed = hops[:3] + [_n(_step("extract", {"question": "x"}, "From p1:\n| A |", url=p1), 4)]
    assert _repeat_reason({"steps": mixed}, "click", {"ref": "e178"}, p1) is None


# ---------------------------------------------------------------- reload in "new message" tasks, cart rules (24 Nov)

def test_a_reload_lets_the_agent_read_a_chat_again():
    from webagent.agent.graph import _read_before
    chat, q = "https://chat.test/c/1", "the newest message: sender, time, text"
    ex = _n(_step("extract", {"question": q}, "From chat:\nAli 10:02: hi", url=chat), 1)
    reload = _n(_step("go_to", {"url": chat}, "Opened " + chat, url=chat), 2)
    new_task = {"task": "Did Ali send me a new message?", "steps": [ex, reload]}
    assert _read_before(new_task, chat, q) is None                        # reloaded: new messages may be there
    assert _read_before({**new_task, "steps": [ex]}, chat, q) == 1         # no reload: refused
    # the second look the checker asks for works on any task
    shop = {"task": "price of the T14", "second_look": True, "steps": [ex, reload]}
    assert _read_before(shop, chat, q) is None
    # a shopping task: going back to page 1 from page 2 is not a reload, still refused
    p1, p2 = "https://shop.test/s", "https://shop.test/s?page=2"
    loop = [_n(_step("extract", {"question": q}, "| A | $1 |", url=p1), 1),
            _n(_step("click", {"ref": "e9"}, "Now at " + p2, url=p2), 2),
            _n(_step("go_to", {"url": p1}, "Opened " + p1, url=p1), 3)]
    assert _read_before({"task": "the cheapest laptop", "steps": loop}, p1, q) == 1
    # even in a messages task, coming back from another page is not a reload
    assert _read_before({"task": "any new messages?", "steps": loop}, p1, q) == 1


def _cart_ctx(monkeypatch, item, verdict):
    from types import SimpleNamespace
    from webagent.browser.session import Snapshot
    from webagent.agent import graph as G
    seen = []
    monkeypatch.setattr(G, "_chat_json", lambda ctx, system, user, **k: seen.append(user) or verdict)
    ctx = SimpleNamespace(browser=SimpleNamespace(item_text=lambda ref: item))
    snap = Snapshot(url="https://shop.test/s", title="", text="", refs={
        "e5": {"role": "button", "name": "Add to Cart"}, "e6": {"role": "link", "name": "HP Pavilion 15"},
        "e7": {"role": "button", "name": "أضف إلى السلة"}})
    return G, ctx, snap, seen


def test_add_to_cart_is_refused_when_the_item_breaks_the_task_rules(monkeypatch):
    task = {"task": "Search for Lenovo laptops under $800 with 16GB RAM and add the best to the cart.", "steps": []}
    G, ctx, snap, seen = _cart_ctx(monkeypatch, "HP Pavilion 15 | 16 GB RAM | $699 | Add to Cart",
                                   {"ok": False, "problem": "The task asks for Lenovo; this is an HP laptop."})
    args = T.validate("click", {"ref": "e5", "why": "add it"})
    r = G._breaks_rules(ctx, task, "click", args, snap)
    assert "Not added" in r and "Lenovo" in r
    assert "HP Pavilion 15" in seen[0] and "Lenovo" in seen[0]          # the item text and the task were checked
    # Arabic button too
    assert G._breaks_rules(ctx, task, "click", T.validate("click", {"ref": "e7", "why": "x"}), snap)
    # other clicks are not checked
    assert G._breaks_rules(ctx, task, "click", T.validate("click", {"ref": "e6", "why": "x"}), snap) == ""
    assert len(seen) == 2


def test_add_to_cart_goes_on_when_the_item_fits_or_the_check_fails(monkeypatch):
    task = {"task": "Add a Lenovo laptop under $800 to the cart.", "steps": []}
    args = T.validate("click", {"ref": "e5", "why": "add it"})
    for verdict in ({"ok": True, "rules": ["Lenovo", "under $800"]}, {}):     # fits / the model call failed
        G, ctx, snap, _ = _cart_ctx(monkeypatch, "Lenovo IdeaPad 5 | 16 GB | $749", verdict)
        assert G._breaks_rules(ctx, task, "click", args, snap) == ""
    # no item text (a fake or an old browser): not checked here, the answer check still looks at the cart
    G, ctx, snap, seen = _cart_ctx(monkeypatch, "", {"ok": False})
    assert G._breaks_rules(ctx, task, "click", args, snap) == "" and seen == []


def test_added_to_cart_in_arabic_is_seen():
    from webagent.agent.graph import ADD_BUTTON, ADDED
    assert ADD_BUTTON.search("أضف إلى السلة") and ADD_BUTTON.search("اضف للسلة") and ADD_BUTTON.search("Add to Cart")
    assert ADDED.search("تمت الإضافة إلى السلة") and ADDED.search("Added to Cart")
    assert not ADDED.search("تمت إضافة تعليقك")


# ---------------------------------------------------------------- gaps found 24 Nov (Arabic labels, chat second look)

@pytest.mark.parametrize("name", ["إرسَال", "أرسِل", "للإرسال", "اشترِ الآن", "إتمام عملية الشراء", "متابعة لإتمام الشراء",
                                  "تقديم الطلب", "قدّم طلبك", "ضع طلبك", "أكمل الطلب", "غرّد", "مسح الكل", "إلغاء الطلب",
                                  "سجّل الآن", "التسجيل", "حفظ", "تبرّع", "تثبيت", "تفعيل", "إلغاء الحجز", "استرداد"])
def test_arabic_labels_with_vowels_prefixes_and_new_words_need_approval(name):
    assert safety.risky_reason({"role": "button", "name": name})


@pytest.mark.parametrize("name", ["تسجيل الدخول إلى حسابك", "مسح البحث", "سجل الطلبات", "أضف إلى السلة", "إغلاق",
                                  "مُجدّد - ممتاز", "رد", "عرض المزيد"])
def test_arabic_harmless_labels_stay_free(name):
    assert safety.risky_reason({"role": "button", "name": name}) is None


def test_arabic_order_link_needs_approval_but_a_plain_link_does_not():
    assert safety.risky_reason({"role": "link", "name": "قدّم طلبك الآن"})
    assert safety.risky_reason({"role": "link", "name": "الإلكترونيات"}) is None


def test_checker_sees_the_screen_text_first():
    from webagent.agent.graph import _page_for_check
    text = "URL: x\n" + "\n".join(f"e{i} [link] \"Menu {i}\"" for i in range(300)) + \
           "\nText in view now (what is on screen): Lina Just now 4:30 PM works for me."
    page = _page_for_check(text)
    assert page.startswith("Text in view now") and "4:30 PM" in page and len(page) <= 3100
    assert _page_for_check("short page") == "short page"


def test_second_look_reloads_the_chat_in_code(monkeypatch):
    from types import SimpleNamespace
    from webagent.agent import graph as G
    chat = "http://127.0.0.1:8766/inbox.html#"
    opened = []
    browser = SimpleNamespace(url=chat, goto=lambda u: opened.append(u))
    ctx = SimpleNamespace(on_step=lambda s: None, browser=browser, snap=object())
    monkeypatch.setitem(G.CONTEXTS, "t-look", ctx)
    q = "messages from Lina"
    steps = [_n(_step("extract", {"question": q}, "From chat:\n| Saif | 20 min | Lina, what time? |", url=chat), 1),
             _n(_step("finish", {"answer": "No"}, "Finished.", url=chat), 2)]
    state = {"task": "Has Lina replied to my question?", "answer": "No, Lina has not replied.", "steps": steps}
    out = G.check(state, {"configurable": {"thread_id": "t-look"}})
    assert opened == [chat] and ctx.snap is None                 # reloaded by the code, not left to the model
    assert out["second_look"] and "was just reloaded" in out["feedback"] and out["answer"] == ""
    # reading the same page again after that reload is allowed
    later = {**state, **out}
    assert G._read_before(later, chat, q) is None


def test_out_of_steps_hint_says_to_finish_not_to_fix_json():
    from webagent.agent.graph import FINAL_TOOLS, _invalid_call_hint
    assert "out of browsing steps" in _invalid_call_hint(set(FINAL_TOOLS))
    assert "valid JSON" in _invalid_call_hint({"click", "finish", "note"})
