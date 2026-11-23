"""MLflow tracing: every LangGraph node and every Groq call becomes a span.

Tracing must never break a task: any problem here only logs a warning.
"""
from __future__ import annotations

import contextlib
import logging

from webagent.config import settings

log = logging.getLogger(__name__)
_enabled = False


def setup() -> bool:
    global _enabled
    if _enabled or not settings.tracing:
        return _enabled
    try:
        import os

        os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")  # keep worker logs clean
        import mlflow

        mlflow.set_tracking_uri(settings.mlflow_uri)
        mlflow.set_experiment("web-agent")
        try:
            mlflow.langchain.autolog()  # LangGraph runs on langchain-core: nodes become spans
        except Exception as exc:
            log.warning("MLflow LangGraph autolog is off: %s", exc)
        try:
            mlflow.groq.autolog()  # every Groq chat call with tokens
        except Exception as exc:
            log.warning("MLflow Groq autolog is off: %s", exc)
        _enabled = True
    except Exception as exc:
        log.warning("MLflow tracing is off: %s", exc)
    return _enabled


@contextlib.contextmanager
def task_trace(task_id: str, request: str):
    """One top-level trace per task, tagged with the task id so it can be found in the MLflow UI."""
    if not setup():
        yield
        return
    import mlflow

    try:
        span_cm = mlflow.start_span(name=f"task {task_id}", attributes={"task_id": task_id, "request": request[:500]})
    except Exception as exc:
        log.warning("MLflow span failed: %s", exc)
        yield
        return
    with span_cm as span:
        try:
            mlflow.update_current_trace(tags={"task_id": task_id})
        except Exception:
            pass
        yield span
