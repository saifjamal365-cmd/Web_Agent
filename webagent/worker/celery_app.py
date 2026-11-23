"""Celery worker: one browser task at a time per worker process.

Windows: celery -A webagent.worker.celery_app worker --pool=solo -l info
Linux:   celery -A webagent.worker.celery_app worker --concurrency=1 -l info  (scale with more workers)
"""
from __future__ import annotations

import logging

from celery import Celery
from celery.signals import worker_ready

from webagent.config import settings

app = Celery("webagent", broker=settings.redis_url)
app.conf.update(
    task_serializer="json", accept_content=["json"], worker_prefetch_multiplier=1, task_acks_late=False,
    broker_connection_retry_on_startup=True, worker_hijack_root_logger=False,
    task_default_queue="webagent",
    beat_schedule={"watches-every-minute": {"task": "webagent.watch_tick", "schedule": 60.0,
                                            "options": {"queue": "webagent", "expires": 55}}},
)
log = logging.getLogger(__name__)


@app.task(name="webagent.watch_tick")
def watch_tick() -> int:
    from webagent.scheduler import tick

    return len(tick())


@app.task(name="webagent.run_task")
def run_task_job(task_id: str, resume: bool = False) -> str:
    from webagent.runner import run_task

    result = run_task(task_id, resume=resume)
    return result.get("status", "unknown") if result else "missing"


@worker_ready.connect
def _resume_unfinished(**_):
    """Tasks that were running when a worker stopped continue from their LangGraph checkpoint."""
    from webagent.store import db

    for t in db.list_tasks(limit=200):
        if t["status"] in ("running", "waiting_approval", "waiting_user"):
            last = db.last_interaction(t["id"])
            if t["status"] != "running" and last and last["parked"] and not last["decided_at"]:
                continue  # waits for the person without a browser: their answer starts it
            log.info("Resuming unfinished task %s", t["id"])
            run_task_job.apply_async(args=[t["id"], True], queue="webagent")
