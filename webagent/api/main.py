"""FastAPI: create tasks, follow them, approve or reply, cancel; serves the dashboard.

Run: uvicorn webagent.api.main:app --port 8020
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field, field_validator

from webagent import files, scheduler
from webagent.agent import safety
from webagent.config import ROOT, settings
from webagent.store import db
from webagent.worker import dispatch

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("webagent.api")

app = FastAPI(title="Web Agent", version="1.0.0")
WEB = ROOT / "web"


def require_key(x_api_key: str | None = Header(default=None)) -> None:
    if settings.api_key and not (x_api_key and secrets.compare_digest(x_api_key, settings.api_key)):
        raise HTTPException(401, "Missing or wrong X-API-Key header.")


class NewTask(BaseModel):
    request: str = Field(min_length=3, max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=36)
    start_url: str | None = Field(default=None, max_length=2000)

    @field_validator("request")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("Write a task of at least 3 characters.")
        return v

    @field_validator("start_url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        if v and not v.strip().lower().startswith(("http://", "https://")):
            raise ValueError("start_url must start with http:// or https://")
        return v.strip() if v else None


class Approval(BaseModel):
    approved: bool
    note: str | None = Field(default=None, max_length=500)


class ReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    log.info("Web Agent API ready: %s", settings.describe())
    if settings.inline:
        # In inline mode the API process is also the worker: continue unfinished tasks.
        for t in db.list_tasks(limit=200):
            last = db.last_interaction(t["id"])
            if t["status"].startswith("waiting") and last and last["parked"] and not last["decided_at"]:
                continue  # waits for the person without a browser: their answer starts it
            if t["status"] in ("queued", "running", "waiting_approval", "waiting_user"):
                dispatch.submit(t["id"], resume=t["status"] != "queued")
        scheduler.start_inline()
    yield


app.router.lifespan_context = lifespan


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/health")
def health() -> dict:
    database = db.health()
    queue = dispatch.queue_ok()
    ok = database == "ok" and queue in ("ok", "inline") and bool(settings.groq_api_key)
    return {"ok": ok, "database": database, "queue": queue, **settings.describe()}


@app.post("/api/tasks", dependencies=[Depends(require_key)], status_code=201)
def create_task(body: NewTask) -> dict:
    task = db.create_task(body.request, body.conversation_id, body.start_url)
    try:
        dispatch.submit(task["id"])
    except Exception as exc:
        db.update_task(task["id"], status="failed", error=f"Could not queue the task: {type(exc).__name__}")
        raise HTTPException(503, "The task queue is not reachable. Is Redis (Memurai) running?") from exc
    return task


@app.get("/api/tasks", dependencies=[Depends(require_key)])
def list_tasks(conversation_id: str | None = None, limit: int = 50) -> list[dict]:
    return db.list_tasks(conversation_id, min(max(limit, 1), 200))


def _task_or_404(task_id: str) -> dict:
    t = db.get_task(task_id)
    if not t:
        raise HTTPException(404, "No such task.")
    return t


@app.get("/api/tasks/{task_id}", dependencies=[Depends(require_key)])
def get_task(task_id: str) -> dict:
    t = _task_or_404(task_id)
    return {**t, "steps": db.get_steps(task_id), "pending": db.pending_interaction(task_id)}


@app.post("/api/tasks/{task_id}/approve", dependencies=[Depends(require_key)])
def approve(task_id: str, body: Approval) -> dict:
    _task_or_404(task_id)
    if not db.answer_interaction(task_id, {"approved": body.approved, "note": body.note or ""}, kind="approval"):
        raise HTTPException(409, "This task is not waiting for an approval.")
    _continue_if_parked(task_id)
    return {"ok": True}


@app.post("/api/tasks/{task_id}/reply", dependencies=[Depends(require_key)])
def reply(task_id: str, body: ReplyIn) -> dict:
    _task_or_404(task_id)
    if not db.answer_interaction(task_id, {"text": body.text}, kind="question"):
        raise HTTPException(409, "This task is not waiting for a reply.")
    _continue_if_parked(task_id)
    return {"ok": True}


def _continue_if_parked(task_id: str) -> None:
    """The task gave its browser back while it waited: queue it again to continue from its checkpoint."""
    last = db.last_interaction(task_id)
    if last and last["parked"]:
        db.update_task(task_id, status="queued")
        dispatch.submit(task_id, resume=True)


@app.post("/api/tasks/{task_id}/cancel", dependencies=[Depends(require_key)])
def cancel(task_id: str) -> dict:
    t = _task_or_404(task_id)
    if t["status"] in db.FINAL:
        return {"ok": True, "status": t["status"]}
    last = db.last_interaction(task_id)
    if t["status"] == "queued" or (last and last["parked"] and not last["decided_at"]):
        # nothing holds this task (queued, or waiting without a browser): stop it right here
        if last and not last["decided_at"]:
            db.answer_interaction(task_id, {"cancelled": True})
        db.update_task(task_id, status="cancelled", cancel_requested=True, answer="Cancelled by the person.")
    else:
        db.update_task(task_id, cancel_requested=True)
    return {"ok": True}


@app.get("/api/tasks/{task_id}/screen", dependencies=[Depends(require_key)])
def screen(task_id: str) -> Response:
    path = Path(settings.live_dir) / task_id / "latest.jpg"
    headers = {"Cache-Control": "no-store"}
    if path.exists():
        try:
            return Response(path.read_bytes(), media_type="image/jpeg", headers=headers)
        except OSError:
            pass
    return Response(status_code=204, headers=headers)


@app.get("/api/skills", dependencies=[Depends(require_key)])
def skills() -> list[dict]:
    return db.list_skills()


class NewWatch(BaseModel):
    request: str = Field(min_length=3, max_length=2000)
    every_minutes: int = Field(ge=5, le=60 * 24 * 7, description="How often to check, in minutes (5 min to 7 days)")
    conversation_id: str | None = Field(default=None, max_length=36)


@app.post("/api/watches", dependencies=[Depends(require_key)], status_code=201)
def create_watch(body: NewWatch) -> dict:
    w = db.create_watch(body.request.strip(), body.every_minutes, body.conversation_id)
    if settings.inline:
        scheduler.start_inline()
    started = scheduler.tick()  # first run now
    return {**db.get_watch(w["id"]), "started_task": started[0] if started else None}


@app.get("/api/watches", dependencies=[Depends(require_key)])
def list_watches() -> list[dict]:
    return db.list_watches()


@app.delete("/api/watches/{watch_id}", dependencies=[Depends(require_key)])
def stop_watch(watch_id: str) -> dict:
    if not db.stop_watch(watch_id):
        raise HTTPException(404, "No such watch.")
    return {"ok": True}


# ---------------------------------------------------------------- files (for upload tasks; downloads of tasks)

MAX_UPLOAD = 25 * 1024 * 1024


@app.post("/api/files", dependencies=[Depends(require_key)], status_code=201)
async def upload_file(request: Request, name: str) -> dict:
    """Give the agent a file (raw body, ?name=cv.pdf). It goes to the inbox; the agent can attach it to forms."""
    clean = files.safe_name(name)
    if files.blocked(clean):
        raise HTTPException(400, "Program files are not accepted.")
    data = await request.body()
    if not data:
        raise HTTPException(400, "The file is empty.")
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Files up to 25 MB.")
    path = files.unique_path(files.INBOX, clean)
    path.write_bytes(data)
    return {"name": path.name, "where": "inbox", "size": len(data)}


@app.get("/api/files", dependencies=[Depends(require_key)])
def list_files(task_id: str = "none") -> list[dict]:
    return files.list_files(task_id)


@app.get("/api/files/{where}/{name}", dependencies=[Depends(require_key)])
def get_file(where: str, name: str, task_id: str = "none") -> FileResponse:
    folder = files.INBOX if where == "inbox" else files.downloads_dir(task_id) if where == "downloads" else None
    if folder is None:
        raise HTTPException(404, "No such folder.")
    path = (folder / files.safe_name(name)).resolve()
    if path.parent != folder.resolve() or not path.is_file():
        raise HTTPException(404, "No such file.")
    return FileResponse(path, filename=path.name)


@app.delete("/api/files/inbox/{name}", dependencies=[Depends(require_key)])
def delete_file(name: str) -> dict:
    path = (files.INBOX / files.safe_name(name)).resolve()
    if path.parent != files.INBOX.resolve() or not path.is_file():
        raise HTTPException(404, "No such file.")
    path.unlink()
    return {"ok": True}


# ---------------------------------------------------------------- saved logins (the person logs in, never the agent)

class LoginIn(BaseModel):
    url: str = Field(min_length=4, max_length=2000)


@app.post("/api/logins", dependencies=[Depends(require_key)], status_code=202)
def open_login_window(body: LoginIn) -> dict:
    """Open a visible browser window on this computer at the site; the person logs in and closes it.
    Only the cookies of that website are kept (data/sessions/<site>.json)."""
    url = body.url.strip()
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    reason = safety.domain_reason(url)
    if reason:
        raise HTTPException(400, reason)
    if sys.platform != "win32" and not os.environ.get("DISPLAY"):
        raise HTTPException(501, "No screen on this server: run scripts/login.py on a computer with a screen and "
                                 "copy data/sessions/<site>.json here.")
    site = safety.domain_of(url)
    if settings.agent_edge:  # sign in right in the agent's Edge window (works for Google too)
        from webagent.browser import agent_edge
        if not agent_edge.open_tab(url):
            raise HTTPException(503, "Could not open the agent's Edge window. Is Microsoft Edge installed?")
        return {"ok": True, "site": site, "agent_edge": True, "message": AGENT_EDGE_SIGNIN.format(site=site)}
    st = _login_status(site)
    if st.get("state") == "open" and st.get("age", 1e9) < 15 * 60:
        return {"ok": True, "site": site, "already_open": True,
                "message": "The login window is already open on the computer that runs the agent."}
    subprocess.Popen([sys.executable, str(ROOT / "scripts" / "login.py"), url], cwd=str(ROOT))
    return {"ok": True, "site": site,
            "message": "A login window opened on the computer that runs the agent. Log in, then close it."}


LOGIN_STATUS = files.DATA / "login_status"
AGENT_EDGE_SIGNIN = ("The {site} sign-in page is in the agent's Edge window (a normal Edge window, it may be behind "
                     "this one). Sign in there yourself, then reply \"done\". You do this only once: that window "
                     "remembers it.")


def _login_status(site: str) -> dict:
    path = LOGIN_STATUS / (files.safe_name(site) + ".json")
    try:
        st = json.loads(path.read_text(encoding="utf-8"))
        st["age"] = (datetime.now(timezone.utc) - datetime.fromisoformat(st["at"])).total_seconds()
        return st
    except (OSError, ValueError, KeyError):
        return {}


@app.get("/api/logins/{site}/status", dependencies=[Depends(require_key)])
def login_status(site: str) -> dict:
    """What happened in the last login window for this site: open, saved or not_signed_in."""
    if settings.agent_edge:
        return {"site": site, "state": "agent_edge", "message": AGENT_EDGE_SIGNIN.format(site=site), "age": None}
    st =_login_status(safety.domain_of(site) or site)
    return {"site": site, "state": st.get("state", ""), "message": st.get("message", ""), "age": st.get("age")}


@app.get("/api/logins", dependencies=[Depends(require_key)])
def list_logins() -> list[dict]:
    files.SESSIONS.mkdir(parents=True, exist_ok=True)
    return [{"site": f.stem, "saved": datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).isoformat()}
            for f in sorted(files.SESSIONS.glob("*.json"))]


@app.delete("/api/logins/{site}", dependencies=[Depends(require_key)])
def forget_login(site: str) -> dict:
    path = (files.SESSIONS / (files.safe_name(site) + ".json")).resolve()
    if path.parent != files.SESSIONS.resolve() or not path.is_file():
        raise HTTPException(404, "No saved login for that site.")
    path.unlink()
    return {"ok": True}
