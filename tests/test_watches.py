"""Watches: repeated tasks that notice when the answer changes."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from webagent import scheduler
from webagent.api import main
from webagent.store import db


@pytest.fixture()
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(main.dispatch, "submit", lambda task_id, resume=False: out.append(task_id))
    yield out
    for w in db.list_watches():  # never leave active watches behind for other tests
        db.stop_watch(w["id"])


def _make_due(watch_id):
    with db.session() as s:
        s.get(db.Watch, watch_id).next_run_at = db.now() - timedelta(seconds=1)


def test_first_run_then_waits_for_the_interval(sent):
    w = db.create_watch("Price of the laptop on the shop", 60)
    started = scheduler.tick()
    assert len(started) == 1 and sent == started
    task = db.get_task(started[0])
    assert task["request"] == "Price of the laptop on the shop"  # first run: no previous answer yet
    assert task["conversation_id"] == w["conversation_id"]
    again = db.get_watch(w["id"])
    assert again["runs"] == 1 and again["last_task_id"] == started[0]
    assert scheduler.tick() == []  # not due for another hour


def test_busy_watch_is_not_started_twice(sent):
    w = db.create_watch("Check the opening hours", 5)
    first = scheduler.tick()
    _make_due(w["id"])
    assert scheduler.tick() == []  # the first task is still queued
    db.update_task(first[0], status="done", answer="9 to 5")
    _make_due(w["id"])
    assert len(scheduler.tick()) == 1


def test_changed_answer_is_flagged(sent):
    w = db.create_watch("Laptop price", 60)
    t1 = scheduler.tick()[0]
    db.record_watch_result(w["id"], "done", "The laptop costs 999 USD.")
    assert db.get_watch(w["id"])["changed"] is False
    db.update_task(t1, status="done")
    _make_due(w["id"])
    t2 = scheduler.tick()[0]
    req = db.get_task(t2)["request"]
    assert "999 USD" in req and "CHANGED:" in req and "SAME:" in req
    db.record_watch_result(w["id"], "done", "SAME: still 999 USD.")
    assert db.get_watch(w["id"])["changed"] is False
    db.record_watch_result(w["id"], "done", "CHANGED: now 899 USD.")
    got = db.get_watch(w["id"])
    assert got["changed"] is True and got["changed_at"] and got["last_answer"].startswith("CHANGED")


def test_failed_run_keeps_the_last_good_answer(sent):
    w = db.create_watch("Laptop price", 60)
    db.record_watch_result(w["id"], "done", "999 USD")
    db.record_watch_result(w["id"], "failed", "The site did not load.")
    got = db.get_watch(w["id"])
    assert got["last_answer"] == "999 USD" and got["last_status"] == "failed" and got["changed"] is False


def test_watch_api(sent):
    with TestClient(main.app) as c:
        r = c.post("/api/watches", json={"request": "Python version on python.org", "every_minutes": 1440})
        assert r.status_code == 201
        w = r.json()
        assert w["started_task"] and w["started_task"] in sent and w["runs"] == 1
        assert [x["id"] for x in c.get("/api/watches").json()] == [w["id"]]
        assert c.post("/api/watches", json={"request": "too often", "every_minutes": 1}).status_code == 422
        assert c.delete(f"/api/watches/{w['id']}").json()["ok"]
        assert c.get("/api/watches").json() == []
        assert c.delete("/api/watches/nope").status_code == 404
