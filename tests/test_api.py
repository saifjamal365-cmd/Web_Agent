"""The HTTP API (FastAPI), with the worker replaced by a recorder."""
import pytest
from fastapi.testclient import TestClient

from webagent.api import main
from webagent.store import db


@pytest.fixture()
def client(monkeypatch):
    sent = []
    monkeypatch.setattr(main.dispatch, "submit", lambda task_id, resume=False: sent.append(task_id))
    with TestClient(main.app) as c:
        c.sent = sent
        yield c


def test_dashboard_and_health(client):
    r = client.get("/")
    assert r.status_code == 200 and "Web Agent" in r.text
    h = client.get("/api/health").json()
    assert h["database"] == "ok" and h["model"] == "openai/gpt-oss-120b"
    assert "chars" in h["groq_key"] and "test-key" not in str(h)


def test_create_list_get(client):
    r = client.post("/api/tasks", json={"request": "  Find the price of a laptop  "})
    assert r.status_code == 201
    t = r.json()
    assert t["request"] == "Find the price of a laptop" and t["status"] == "queued"
    assert client.sent[-1] == t["id"]
    listed = client.get(f"/api/tasks?conversation_id={t['conversation_id']}").json()
    assert [x["id"] for x in listed] == [t["id"]]
    d = client.get(f"/api/tasks/{t['id']}").json()
    assert d["steps"] == [] and d["pending"] is None


def test_validation(client):
    assert client.post("/api/tasks", json={"request": "  "}).status_code == 422
    assert client.post("/api/tasks", json={"request": "ok task", "start_url": "file:///c:/x"}).status_code == 422
    assert client.get("/api/tasks/nope").status_code == 404


def test_approve_and_reply_need_an_open_question(client):
    t = client.post("/api/tasks", json={"request": "Buy a mouse"}).json()
    assert client.post(f"/api/tasks/{t['id']}/approve", json={"approved": True}).status_code == 409
    db.open_interaction(t["id"], "approval", "Buy now?", {"what": "Click Buy now"})
    assert client.get(f"/api/tasks/{t['id']}").json()["pending"]["kind"] == "approval"
    assert client.post(f"/api/tasks/{t['id']}/approve", json={"approved": False, "note": "no"}).status_code == 200
    assert client.post(f"/api/tasks/{t['id']}/reply", json={"text": "hi"}).status_code == 409


def test_cancel_queued_task(client):
    t = client.post("/api/tasks", json={"request": "Long task"}).json()
    assert client.post(f"/api/tasks/{t['id']}/cancel").json()["ok"]
    assert client.get(f"/api/tasks/{t['id']}").json()["status"] == "cancelled"


def test_screen_is_empty_before_the_task_runs(client):
    t = client.post("/api/tasks", json={"request": "Anything"}).json()
    assert client.get(f"/api/tasks/{t['id']}/screen").status_code == 204


def test_api_key_is_enforced(client, monkeypatch):
    from types import SimpleNamespace
    fake = SimpleNamespace(**{**main.settings.__dict__, "api_key": "s3cret"})
    monkeypatch.setattr(main, "settings", fake)
    assert client.get("/api/tasks").status_code == 401
    assert client.get("/api/tasks", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/api/tasks", headers={"X-API-Key": "s3cret"}).status_code == 200


def test_answer_to_a_parked_task_queues_it_again_and_stop_ends_it(client):
    t = client.post("/api/tasks", json={"request": "Check my account"}).json()
    client.sent.clear()
    iid = db.open_interaction(t["id"], "question", "Log in?", {"tool": "login", "site": "example.com"})
    db.update_task(t["id"], status="waiting_user")
    assert db.park_interaction(iid)
    assert client.post(f"/api/tasks/{t['id']}/reply", json={"text": "done"}).status_code == 200
    assert client.sent == [t["id"]] and client.get(f"/api/tasks/{t['id']}").json()["status"] == "queued"
    # a parked task that nobody answered is stopped at once (no worker holds it)
    t2 = client.post("/api/tasks", json={"request": "Check my orders"}).json()
    iid2 = db.open_interaction(t2["id"], "question", "Log in?", {})
    db.update_task(t2["id"], status="waiting_user")
    db.park_interaction(iid2)
    assert client.post(f"/api/tasks/{t2['id']}/cancel").json()["ok"]
    assert client.get(f"/api/tasks/{t2['id']}").json()["status"] == "cancelled"
    # an answer that came before parking: the waiting worker keeps it, nothing is queued again
    t3 = client.post("/api/tasks", json={"request": "Buy"}).json()
    client.sent.clear()
    iid3 = db.open_interaction(t3["id"], "approval", "Buy?", {})
    assert client.post(f"/api/tasks/{t3['id']}/approve", json={"approved": True}).status_code == 200
    assert not db.park_interaction(iid3) and client.sent == []
