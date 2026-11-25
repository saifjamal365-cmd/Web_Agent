"""End to end: the LangGraph agent with a scripted fake model drives the real browser on the test website."""
import threading
import time

from conftest import FakeLLM, ref_for
from webagent.runner import run_task
from webagent.store import db


def _answer_when_asked(task_id, answer, kind="approval", timeout=40):
    """Play the person: wait until the task asks, then answer like the dashboard does."""
    def go():
        end = time.time() + timeout
        while time.time() < end:
            p = db.pending_interaction(task_id)
            if p and p["kind"] == kind:
                db.answer_interaction(task_id, answer, kind=kind)
                return
            time.sleep(0.2)
    t = threading.Thread(target=go, daemon=True)
    t.start()
    return t


def test_lookup_task_finds_price_and_finishes(site_url):
    llm = FakeLLM([
        ("go_to", {"url": site_url + "/index.html"}),
        ("type", lambda page: {"ref": ref_for(page, "Search products"), "text": "laptop", "submit": True}),
        ("click", lambda page: {"ref": ref_for(page, "Aero Laptop 14", role="link")}),
        ("note", {"fact": "Aero Laptop 14 costs 999.00 JOD", "source": site_url + "/product-laptop.html"}),
        ("finish", {"answer": "The Aero Laptop 14 costs 999.00 JOD.", "sources": [site_url + "/product-laptop.html"]}),
    ])
    t = db.create_task("What does the Aero Laptop cost on the test shop?", None, None)
    out = run_task(t["id"], llm=llm, poll=0.1)
    assert out["status"] == "done", out
    assert "999.00 JOD" in out["answer"]
    assert out["plan"] == ["Open the shop", "Find the item", "Answer"]
    tools = [s["tool"] for s in db.get_steps(t["id"])]
    assert tools[:5] == ["go_to", "type", "click", "note", "finish"]
    assert "check" in tools and "learn" in tools
    assert out["tokens"] > 0
    # the lesson was saved for next time
    assert any(s["domain"] == "127.0.0.1" for s in db.list_skills())


def test_buy_waits_for_approval_and_rejection_stops_it(site_url):
    result_page = {}

    def read_status(page):
        result_page["text"] = page
        return {"part": 1}

    llm = FakeLLM([
        ("go_to", {"url": site_url + "/product-laptop.html"}),
        ("click", lambda page: {"ref": ref_for(page, "Buy now")}),
        ("read_page", read_status),
        ("finish", {"answer": "I did not buy it because you rejected the purchase."}),
    ])
    t = db.create_task("Buy the Aero Laptop", None, None)
    _answer_when_asked(t["id"], {"approved": False, "note": "too expensive"})
    out = run_task(t["id"], llm=llm, poll=0.1)
    steps = db.get_steps(t["id"])
    click = next(s for s in steps if s["tool"] == "click")
    assert not click["ok"] and "REJECTED" in click["result"] and "too expensive" in click["result"]
    assert "Order placed" not in result_page["text"]
    assert out["status"] == "done"


def test_buy_runs_after_approval(site_url):
    seen = {}

    def after(page):
        seen["page"] = page
        return {"answer": "Bought.", "sources": []}

    llm = FakeLLM([
        ("go_to", {"url": site_url + "/product-laptop.html"}),
        ("click", lambda page: {"ref": ref_for(page, "Buy now")}),
        ("finish", after),
    ])
    t = db.create_task("Buy the Aero Laptop", None, None)
    _answer_when_asked(t["id"], {"approved": True})
    out = run_task(t["id"], llm=llm, poll=0.1)
    assert out["status"] == "done"
    assert "Order placed for 1 laptop(s)." in seen["page"]
    click = next(s for s in db.get_steps(t["id"]) if s["tool"] == "click")
    assert click["ok"]


def test_rejected_action_is_never_asked_again_and_task_stops(site_url):
    buy = ("click", lambda page: {"ref": ref_for(page, "Buy now")})
    llm = FakeLLM([("go_to", {"url": site_url + "/product-laptop.html"}), buy, buy, ("back", {}),
                   ("go_to", {"url": site_url + "/product-laptop.html"}), buy, buy])
    t = db.create_task("Buy the Aero Laptop", None, None)
    _answer_when_asked(t["id"], {"approved": False})
    out = run_task(t["id"], llm=llm, poll=0.1)
    clicks = [s for s in db.get_steps(t["id"]) if s["tool"] == "click"]
    assert "REJECTED this action" in clicks[0]["result"]
    assert all("already REJECTED" in c["result"] for c in clicks[1:])
    assert out["status"] == "done" and out["answer"].startswith("Not done: you rejected")
    with db.session() as s:  # the person was asked only once
        from webagent.store.db import Interaction
        assert s.query(Interaction).filter_by(task_id=t["id"]).count() == 1


def test_never_types_a_password_even_if_the_page_says_so(site_url):
    llm = FakeLLM([
        ("go_to", {"url": site_url + "/contact.html"}),
        ("type", lambda page: {"ref": ref_for(page, "Password"), "text": "1234"}),
        ("give_up", {"reason": "The page asks for a password; I will not type it."}),
    ])
    t = db.create_task("Send a message to the shop", None, None)
    out = run_task(t["id"], llm=llm, poll=0.1)
    typed = next(s for s in db.get_steps(t["id"]) if s["tool"] == "type")
    assert not typed["ok"] and "never types passwords" in typed["result"]
    assert out["status"] == "failed"


def test_ask_user_waits_for_the_reply(site_url):
    llm = FakeLLM([
        ("ask_user", {"question": "Which product do you mean?"}),
        ("finish", lambda page: {"answer": "OK, the mouse."}),
    ])
    t = db.create_task("Check the product", None, None)
    _answer_when_asked(t["id"], {"text": "the mouse"}, kind="question")
    out = run_task(t["id"], llm=llm, poll=0.1)
    ask = next(s for s in db.get_steps(t["id"]) if s["tool"] == "ask_user")
    assert "the mouse" in ask["result"]
    assert out["status"] == "done"


def test_cancel_stops_the_task(site_url):
    llm = FakeLLM([("go_to", {"url": site_url + "/index.html"})] + [("scroll", {"direction": "down"})] * 30)
    t = db.create_task("Scroll forever", None, None)

    def cancel_soon():
        time.sleep(2)
        db.update_task(t["id"], cancel_requested=True)
    threading.Thread(target=cancel_soon, daemon=True).start()
    out = run_task(t["id"], llm=llm, poll=0.1)
    assert out["status"] == "cancelled"


def test_stuck_agent_reflects_then_stops(site_url):
    llm = FakeLLM([("go_to", {"url": site_url + "/index.html"})] + [("click", {"ref": "e999"})] * 40)
    t = db.create_task("Do something impossible", None, None)
    out = run_task(t["id"], llm=llm, poll=0.1)
    tools = [s["tool"] for s in db.get_steps(t["id"])]
    assert "reflect" in tools
    assert out["status"] == "failed"


def test_failed_check_sends_agent_back(site_url):
    llm = FakeLLM([
        ("go_to", {"url": site_url + "/product-mouse.html"}),
        ("finish", {"answer": "It costs 10 JOD."}),
        ("finish", {"answer": "It costs 15.50 JOD."}),
    ], check_ok=False)
    t = db.create_task("Price of the mouse?", None, None)
    out = run_task(t["id"], llm=llm, poll=0.1)
    checks = [s for s in db.get_steps(t["id"]) if s["tool"] == "check"]
    assert len(checks) == 2
    assert out["status"] == "done" and "check was not fully satisfied" in out["answer"]


def test_stuck_agent_answers_from_its_notes(site_url):
    llm = FakeLLM([("go_to", {"url": site_url + "/product-mouse.html"}),
                   ("note", {"fact": "The Silent Mouse costs 15.50 JOD.", "source": site_url + "/product-mouse.html"})]
                  + [("click", {"ref": "e999"})] * 40, salvage="The Silent Mouse costs 15.50 JOD.")
    t = db.create_task("Price of the mouse?", None, None)
    out = run_task(t["id"], llm=llm, poll=0.1)
    tools = [s["tool"] for s in db.get_steps(t["id"])]
    assert tools.count("reflect") == 3 and "check" in tools
    assert out["status"] == "done" and out["answer"] == "The Silent Mouse costs 15.50 JOD."
    assert out["sources"] == [site_url + "/product-mouse.html"]


def test_stuck_answer_from_notes_must_pass_the_check(site_url):
    llm = FakeLLM([("go_to", {"url": site_url + "/product-mouse.html"}),
                   ("note", {"fact": "A mouse page exists.", "source": site_url + "/product-mouse.html"})]
                  + [("click", {"ref": "e999"})] * 40, check_ok=False, salvage="It costs 99 JOD.")
    t = db.create_task("Price of the mouse?", None, None)
    out = run_task(t["id"], llm=llm, poll=0.1)
    assert out["status"] == "failed" and out["answer"].startswith("The agent got stuck")


def test_watch_runs_never_wait_for_a_person(site_url):
    # Scheduled runs have nobody at the screen: risky actions are refused at once, questions get an automatic reply.
    llm = FakeLLM([
        ("go_to", {"url": site_url + "/product-laptop.html"}),
        ("click", lambda page: {"ref": ref_for(page, "Buy now")}),
        ("ask_user", {"question": "Which colour?"}),
        ("finish", {"answer": "SAME: the laptop costs 999.00 JOD; I did not buy it."}),
    ])
    w = db.create_watch("Laptop price", 60)
    t = db.create_task("Laptop price", w["conversation_id"], None, watch_id=w["id"])
    started = time.time()
    out = run_task(t["id"], llm=llm, poll=0.1)
    assert time.time() - started < 15  # did not wait for the approval timeout
    steps = db.get_steps(t["id"])
    click = next(s for s in steps if s["tool"] == "click")
    assert not click["ok"] and "scheduled run" in click["result"]
    asked = next(s for s in steps if s["tool"] == "ask_user")
    assert "Nobody can answer" in asked["result"]
    assert out["status"] == "done" and db.get_watch(w["id"])["last_answer"].startswith("SAME")
    db.stop_watch(w["id"])


def test_long_wait_gives_the_browser_back_and_the_answer_continues_the_task(site_url, monkeypatch):
    """A task that waits for the person frees the worker; the answer starts it again from its checkpoint."""
    from webagent.config import settings
    monkeypatch.setattr(settings.__class__, "park_after", property(lambda self: 1), raising=False)
    seen = {}

    def after(page):
        seen["page"] = page
        return {"answer": "Bought.", "sources": []}

    llm = FakeLLM([
        ("go_to", {"url": site_url + "/product-laptop.html"}),
        ("click", lambda page: {"ref": ref_for(page, "Buy now")}),
        ("finish", after),
    ])
    t = db.create_task("Buy the Aero Laptop", None, None)
    started = time.time()
    first = run_task(t["id"], llm=llm, poll=0.1)
    assert first["status"] == "waiting_approval" and time.time() - started < 30   # did not hold the worker
    last = db.last_interaction(t["id"])
    assert last["parked"] and last["decided_at"] is None
    # the person approves later (like POST /approve), then the task continues in a new browser
    assert db.answer_interaction(t["id"], {"approved": True}, kind="approval")
    out = run_task(t["id"], resume=True, llm=llm, poll=0.1)
    assert out["status"] == "done", out
    assert "Order placed for 1 laptop(s)." in seen["page"]


def test_a_login_that_could_not_be_saved_is_not_asked_again():
    from webagent.agent import graph
    from webagent.agent import tools as T
    ctx = graph.Ctx(task_id="t-login", browser=None, llm=None)
    graph.CONTEXTS["t-login"] = ctx
    try:
        state = {"pending": {"name": "login", "args": {"site": "forms.google.com"}, "url": "",
                             "tools": sorted(T.available(groups={"login"}))},
                 "login_failed": ["forms.google.com"], "steps": []}
        out = graph.gate(state, {"configurable": {"thread_id": "t-login"}})
        assert "Do not ask again" in out["decision"]["refused"]
    finally:
        graph.CONTEXTS.pop("t-login", None)


def test_note_called_with_an_answer_is_treated_as_finish(site_url):
    # live Wikipedia run, 25 Nov: note was called 3 times with {"answer": ...}; each refusal cost a step
    llm = FakeLLM([
        ("go_to", {"url": site_url + "/product-laptop.html"}),
        ("note", {"answer": "The Aero Laptop 14 costs 999.00 JOD.", "sources": [site_url + "/product-laptop.html"]}),
    ])
    t = db.create_task("What does the Aero Laptop cost on the test shop?", None, None)
    out = run_task(t["id"], llm=llm, poll=0.1)
    assert out["status"] == "done" and "999.00 JOD" in out["answer"], out
    assert [s["tool"] for s in db.get_steps(t["id"])][:2] == ["go_to", "finish"]


def test_an_answer_written_as_text_is_treated_as_finish(site_url):
    from webagent.llm import Reply

    class TextAnswer(FakeLLM):
        def chat(self, messages, **kw):
            if not kw.get("json_mode") and not self.script and not getattr(self, "said", False):
                self.said = True
                return Reply(text="**Answer:** The Aero Laptop 14 costs 999.00 JOD.", tokens=10)
            return super().chat(messages, **kw)

    llm = TextAnswer([("go_to", {"url": site_url + "/product-laptop.html"})])
    t = db.create_task("What does the Aero Laptop cost on the test shop?", None, None)
    out = run_task(t["id"], llm=llm, poll=0.1)
    assert out["status"] == "done" and out["answer"].startswith("The Aero Laptop 14 costs 999.00 JOD"), out
