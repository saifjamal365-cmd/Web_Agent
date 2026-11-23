"""Send a task to the worker (Celery), or to a background thread when WA_INLINE=1 (development, no Redis)."""
from __future__ import annotations

import logging
import queue
import threading

from webagent.config import settings

log = logging.getLogger(__name__)
_q: "queue.Queue[tuple[str, bool]]" = queue.Queue()
_thread: threading.Thread | None = None


def _loop() -> None:
    from webagent.runner import run_task

    while True:
        task_id, resume = _q.get()
        try:
            run_task(task_id, resume=resume)
        except Exception:
            log.exception("inline task %s failed", task_id)


def submit(task_id: str, resume: bool = False) -> None:
    global _thread
    if settings.inline:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_loop, name="webagent-inline-worker", daemon=True)
            _thread.start()
        _q.put((task_id, resume))
        return
    from webagent.worker.celery_app import run_task_job

    run_task_job.apply_async(args=[task_id, resume], queue="webagent")


def queue_ok() -> str:
    if settings.inline:
        return "inline"
    try:
        import redis

        redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2).ping()
        return "ok"
    except Exception as exc:
        return f"error: {type(exc).__name__}"
