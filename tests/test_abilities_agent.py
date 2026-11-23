"""End to end: the agent uses the new abilities (one-step forms, helper agents) with a scripted fake model.
(A separate file: the browser of test_abilities.py must be closed before run_task starts its own.)"""
import threading
import time

from conftest import FakeLLM, ref_for
from webagent.runner import run_task
from webagent.store import db

def _answer_when_asked(task_id, answer, kind="approval", timeout=40):
    def go():
        end = time.time() + timeout
        while time.time() < end:
            p = db.pending_interaction(task_id)
            if p and p["kind"] == kind:
                db.answer_interaction(task_id, answer, kind=kind)
                return
            time.sleep(0.2)
    threading.Thread(target=go, daemon=True).start()


def test_agent_fills_a_form_in_one_step_then_sends_after_approval(site_url):
    seen = {}

    def finish(page):
        seen["page"] = page
        return {"answer": "The application was sent.", "sources": [site_url + "/forms.html"]}

    llm = FakeLLM([
        ("go_to", {"url": site_url + "/forms.html"}),
        ("fill_form", lambda page: {"fields": [{"ref": ref_for(page, "Full name"), "value": "Omar"},
                                              {"ref": ref_for(page, "Position"), "value": "Cashier"}]}),
        ("click", lambda page: {"ref": ref_for(page, "Submit application")}),
        ("finish", finish),
    ])
    t = db.create_task("Apply for the cashier job as Omar on the test shop", None, None)
    _answer_when_asked(t["id"], {"approved": True})
    out = run_task(t["id"], llm=llm, poll=0.1)
    assert out["status"] == "done", out
    steps = db.get_steps(t["id"])
    fill = next(s for s in steps if s["tool"] == "fill_form")
    assert fill["ok"] and "Filled 2 field(s)" in fill["result"]
    click = next(s for s in steps if s["tool"] == "click")
    assert "Application received. Thank you, Omar!" in click["result"]


def test_delegate_runs_helper_agents_and_collects_their_answers(site_url):
    llm = FakeLLM([
        ("delegate", {"tasks": ["Price of the Silent Mouse on the test shop", "Price of the Aero Laptop 14"]}),
        # helper 1
        ("go_to", {"url": site_url + "/product-mouse.html"}),
        ("finish", {"answer": "The Silent Mouse costs 15.50 JOD.", "sources": [site_url + "/product-mouse.html"]}),
        # helper 2 tries to buy: refused automatically, then finishes
        ("go_to", {"url": site_url + "/product-laptop.html"}),
        ("click", lambda page: {"ref": ref_for(page, "Buy now")}),
        ("finish", {"answer": "The Aero Laptop 14 costs 999.00 JOD.", "sources": [site_url + "/product-laptop.html"]}),
        # the main agent
        ("finish", {"answer": "Mouse 15.50 JOD, laptop 999.00 JOD.", "sources": [site_url + "/product-mouse.html"]}),
    ])
    t = db.create_task("Compare the mouse and laptop prices on the test shop", None, None)
    out = run_task(t["id"], llm=llm, poll=0.1)
    assert out["status"] == "done", out
    steps = db.get_steps(t["id"])
    tools = [s["tool"] for s in steps]
    assert "helper 1: go_to" in tools and "helper 2: click" in tools
    buy = next(s for s in steps if s["tool"] == "helper 2: click")
    assert not buy["ok"] and "helper agent" in buy["result"]  # helpers never buy, nobody is asked
    deleg = next(s for s in steps if s["tool"] == "delegate")
    assert "Helper 1" in deleg["result"] and "15.50 JOD" in deleg["result"] and "999.00 JOD" in deleg["result"]
    assert db.pending_interaction(t["id"]) is None
