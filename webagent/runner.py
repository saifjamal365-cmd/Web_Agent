"""Runs one task from start to end: browser, graph, approvals, saving steps and the live screen."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Callable

from langgraph.types import Command

from webagent import files, tracing
from webagent.agent.graph import CONTEXTS, Ctx, build_graph, checkpointer
from webagent.browser.session import BrowserSession
from webagent.config import settings
from webagent.llm import LLM, GroqLLM, LLMError
from webagent.store import db

log = logging.getLogger(__name__)


def earlier_turns(conversation_id: str, task_id: str) -> list[dict]:
    """Earlier questions and answers in the same chat, each with the files it used, so a follow-up such as "is
    there any text in it?" knows which file "it" is (live 25 Nov: the agent asked for a URL instead)."""
    out = []
    for h in db.conversation_history(conversation_id, task_id):
        answer = h.get("answer") or ""
        used = list(dict.fromkeys(str((s.get("args") or {}).get("file") or "").strip()
                                  for s in db.get_steps(h["id"])
                                  if s.get("tool") in ("look", "read_file", "upload_file"))) if h.get("id") else []
        used = [u for u in used if u]
        if used:
            answer += f"\n  (files used: {', '.join(used[:5])})"
        out.append({"request": h["request"], "answer": answer})
    return out


def default_browser(task_id: str) -> BrowserSession:
    cdp = None
    if settings.agent_edge:
        from webagent.browser import agent_edge
        if not agent_edge.start():
            raise RuntimeError("Could not open the agent's Edge window (is Microsoft Edge installed?).")
        cdp = agent_edge.endpoint()
    return BrowserSession(task_id, headless=settings.headless, channel=settings.browser_channel,
                          live_dir=settings.live_dir, downloads_dir=files.downloads_dir(task_id),
                          sessions_dir=files.SESSIONS, cdp=cdp).start()


UNATTENDED_APPROVAL = {"approved": False, "note": "This is a scheduled run and nobody is here to approve. "
                       "Do not do risky actions: choose the safe option (for example Reject or Close on a banner) "
                       "or finish and say what needs a person."}
UNATTENDED_REPLY = {"text": "Nobody can answer during a scheduled run. Continue without it, or finish with what you have."}


def _answer_unattended(task_id: str, payload: dict) -> dict:
    """Watches run with nobody at the screen: never block the worker; record the question and the automatic answer."""
    kind = payload.get("kind", "approval")
    existing = db.pending_interaction(task_id)
    if not (existing and existing["kind"] == kind):
        db.open_interaction(task_id, kind, payload.get("message", ""), payload.get("action") or {})
    answer = UNATTENDED_APPROVAL if kind == "approval" else UNATTENDED_REPLY
    db.answer_interaction(task_id, {**answer, "automatic": True})
    db.update_task(task_id, status="running")
    return answer


PARKED = {"parked": True}  # the task gave its browser back and waits without a worker


def _wait_for_person(task_id: str, payload: dict, browser: BrowserSession, poll: float,
                     can_park: bool = False) -> dict | None:
    """Open an approval/question in the dashboard and wait. None = cancelled or no answer in time.
    PARKED = the person did not answer within park_after seconds: the worker is freed for other tasks."""
    kind = payload.get("kind", "approval")
    existing = db.pending_interaction(task_id)
    if existing and existing["kind"] == kind:
        iid = existing["id"]
        opened = _epoch(existing["created_at"])  # also counts the wait before a worker restart
    else:
        iid = db.open_interaction(task_id, kind, payload.get("message", ""), payload.get("action") or {})
        opened = time.time()
    answer = _wait(task_id, iid, kind, browser, poll, can_park)
    if answer is PARKED:
        return answer
    if answer is not None:
        # Time spent waiting for the person does not count against the agent's time limit.
        answer = {**answer, "waited": max(0.0, time.time() - opened)}
    return answer


def _epoch(iso: str) -> float:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:  # SQLite keeps UTC without a zone
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _wait(task_id: str, iid: int, kind: str, browser: BrowserSession, poll: float,
          can_park: bool = False) -> dict | None:
    db.update_task(task_id, status="waiting_approval" if kind == "approval" else "waiting_user")
    browser.save_live()
    deadline = time.time() + settings.approval_timeout
    park_at = time.time() + settings.park_after if can_park and settings.park_after > 0 else None
    while time.time() < deadline:
        if park_at and time.time() >= park_at and db.park_interaction(iid):
            log.info("Task %s waits for the person: browser released", task_id)
            return PARKED
        answer = db.interaction_answer(iid)
        if answer is not None:
            db.update_task(task_id, status="running")
            return answer
        if db.cancel_requested(task_id):
            db.answer_interaction(task_id, {"cancelled": True})
            return None
        time.sleep(poll)
    db.answer_interaction(task_id, {"timeout": True})
    if kind == "approval":
        db.update_task(task_id, status="running")
        return {"approved": False, "note": "No decision was made in time."}
    return None


def _restore_page(ctx: Ctx, values: dict) -> None:
    """A task that continues in a new browser goes back to its page first (and reads it, for the refs)."""
    url = values.get("current_url") or ""
    if not url.startswith(("http://", "https://")):
        return
    try:
        ctx.browser.goto(url)
        ctx.snap = ctx.browser.snapshot()
    except Exception as exc:  # the agent sees the page again at its next step anyway
        log.warning("Could not reopen %s: %s", url, exc)


def run_task(task_id: str, *, resume: bool = False, llm: LLM | None = None,
             browser_factory: Callable[[str], BrowserSession] | None = None, poll: float = 1.0,
             search_url: str | None = None) -> dict:
    task = db.get_task(task_id)
    if not task:
        raise ValueError(f"No task {task_id}")
    if task["status"] in db.FINAL:
        return task
    db.update_task(task_id, status="running", error=None)
    tracing.setup()
    try:
        llm = llm or GroqLLM()
    except LLMError as exc:
        db.update_task(task_id, status="failed", error=str(exc), answer=str(exc))
        return db.get_task(task_id)

    browser = None
    try:
        browser = (browser_factory or default_browser)(task_id)

        def on_step(step: dict) -> None:
            db.add_step(task_id, node="act" if step.get("tool") not in ("recall", "check", "reflect", "learn") else step["tool"],
                        tool=step.get("tool"), args=step.get("args"), why=step.get("why"), result=step.get("result"),
                        ok=step.get("ok", True), url=step.get("url") or None)
            browser.save_live()

        ctx = Ctx(task_id=task_id, browser=browser, llm=llm, on_step=on_step,
                  on_tokens=lambda n: db.add_tokens(task_id, n),
                  on_plan=lambda plan: db.update_task(task_id, plan=plan),
                  is_cancelled=lambda: db.cancel_requested(task_id))
        if search_url:
            ctx.search_url = search_url
        CONTEXTS[task_id] = ctx
        graph = build_graph(checkpointer())
        config = {"configurable": {"thread_id": task_id}, "recursion_limit": 1000}
        saved = graph.get_state(config)
        resumed_with = None   # the person's answer given while the task was parked (shown on the trace)
        if resume and saved and saved.values:
            cmd = None  # continue from the last checkpoint
            log.info("Resuming task %s from its checkpoint", task_id)
            _restore_page(ctx, saved.values)
            waiting = any(getattr(t, "interrupts", None) for t in (saved.tasks or ()))
            last = db.last_interaction(task_id)
            if waiting and last and last["parked"] and last["decided_at"] and last["answer"] is not None:
                if last["answer"].get("cancelled"):
                    db.update_task(task_id, status="cancelled", answer="Cancelled by the person.",
                                   error="Cancelled by the person.")
                    return db.get_task(task_id)
                # The person answered while no worker held the task: continue with that answer.
                waited = max(0.0, _epoch(last["decided_at"]) - _epoch(last["created_at"]))
                cmd = Command(resume={**last["answer"], "waited": waited})
                resumed_with = last
                if last["kind"] == "approval":
                    ctx.resume_key = (last["action"] or {}).get("key", "")
        else:
            cmd = {"task_id": task_id, "task": task["request"], "start_url": task.get("start_url") or "",
                   "history": earlier_turns(task["conversation_id"], task_id)}
        result: dict = {}
        with tracing.task_trace(task_id, task["request"]) as trace:
            if resumed_with:
                tracing.person({"kind": resumed_with["kind"], "message": resumed_with.get("message")},
                               resumed_with["answer"])
            while True:
                result = graph.invoke(cmd, config)
                intr = result.get("__interrupt__")
                if not intr:
                    break
                if task.get("watch_id"):
                    answer = _answer_unattended(task_id, intr[0].value)
                else:
                    desk = ctx.env.desktop  # an open desktop app cannot be handed over: wait in place
                    answer = _wait_for_person(task_id, intr[0].value, browser, poll,
                                              can_park=not (desk is not None and desk.windows))
                if answer is PARKED:
                    return db.get_task(task_id)  # the browser closes below; the answer starts the task again
                if answer is None:
                    cancelled = db.cancel_requested(task_id)
                    result = {"status": "cancelled" if cancelled else "failed",
                              "answer": "Cancelled by the person." if cancelled else "No answer from the person in time."}
                    break
                tracing.person(intr[0].value, answer)
                cmd = Command(resume=answer)
            tracing.task_done(trace, result)

        status = result.get("status") or "failed"
        if status == "running":
            status = "failed"
        if db.cancel_requested(task_id) and status != "done":
            status = "cancelled"
        answer = result.get("answer") or ("Cancelled by the person." if status == "cancelled" else "No answer.")
        sources = result.get("sources") or []
        db.update_task(task_id, status=status, answer=answer, sources=sources,
                       error=None if status == "done" else answer)
        if task.get("watch_id"):
            db.record_watch_result(task["watch_id"], status, answer)
    except Exception as exc:
        log.exception("Task %s crashed", task_id)
        db.update_task(task_id, status="failed", error=f"{type(exc).__name__}: {str(exc)[:300]}",
                       answer="The task stopped because of an internal error.")
    finally:
        if browser is not None:
            try:
                browser.save_live()
            except Exception:
                pass
            browser.close()
        CONTEXTS.pop(task_id, None)
    return db.get_task(task_id)
