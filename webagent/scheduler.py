"""Watches: repeated tasks. Every minute, due watches become normal tasks (Celery Beat, or a thread in inline mode)."""
from __future__ import annotations

import logging
import threading
import time

from webagent.store import db

log = logging.getLogger(__name__)


def watch_request(w: dict) -> str:
    if not w.get("last_answer"):
        return w["request"]
    return (f"{w['request']}\n\n(This is a repeated check. The previous answer was: \"{w['last_answer'][:800]}\". "
            "Check again on the website now. Start your final answer with 'CHANGED:' if the result is different "
            "from the previous answer, or 'SAME:' if it is the same.)")


def tick() -> list[str]:
    """Start a task for every watch that is due. Returns the new task ids."""
    from webagent.worker import dispatch

    started = []
    for w in db.claim_due_watches():
        t = db.create_task(watch_request(w), w["conversation_id"], None, watch_id=w["id"])
        db.set_watch_task(w["id"], t["id"])
        dispatch.submit(t["id"])
        started.append(t["id"])
        log.info("Watch %s started task %s", w["id"], t["id"])
    return started


_thread: threading.Thread | None = None


def start_inline(interval: float = 60.0) -> None:
    """Development mode without Celery Beat: check the watches from a background thread."""
    global _thread
    if _thread and _thread.is_alive():
        return

    def loop():
        while True:
            try:
                tick()
            except Exception:
                log.exception("watch tick failed")
            time.sleep(interval)

    _thread = threading.Thread(target=loop, name="webagent-watches", daemon=True)
    _thread.start()
