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
