"""MLflow tracing: one trace per task that shows who sent what to whom.

The trace of a task reads like a conversation between the agent's parts:

    task <id>                                   the person's request -> the final answer
      recall · memory                           lessons found for this task
      plan · planner (gpt-oss)                  the plan
        LLM gpt-oss-120b <- plan                the exact prompt sent and the reply
      route: plan -> decide
      decide · brain (gpt-oss)                  receives the last result, sends a tool call to the gate
        LLM gpt-oss-120b <- decide
      route: decide -> gate
      gate · safety gate (code)                 allowed / refused / asks the person
      person -> agent                           the person's answer or approval (when asked)
      execute · hands (browser and tools)
        tool · look                             the tool's arguments and result
          LLM qwen3.8-27b (vision) <- look      the image went to the vision model
      route: execute -> check
      check · checker (gpt-oss)                 "Answer OK" or what to fix, sent back to decide
      ...

Each span's inputs are what that part received and its outputs what it sent on; the attribute "says" is the
message in one line. Model calls carry the exact messages (images shown as "[image, 120 KB]", not stored).

Tracing must never break a task: any problem here only logs a warning.
"""
from __future__ import annotations

import contextlib
import contextvars
import functools
import json
import logging
from typing import Any, Callable

from webagent.config import settings

log = logging.getLogger(__name__)
_enabled = False

# Which part of the agent is talking now (a node or a tool): names the model calls it makes.
_who: contextvars.ContextVar[str] = contextvars.ContextVar("wa_trace_who", default="")

ROLES = {
    "recall": "memory",
    "plan": "planner (gpt-oss)",
    "decide": "brain (gpt-oss)",
    "gate": "safety gate (code)",
    "execute": "hands (browser and tools)",
    "reflect": "reflector (gpt-oss)",
    "check": "checker (gpt-oss)",
    "learn": "memory writer (gpt-oss)",
    "give_up": "give up",
    "stop_rejected": "stop (person said no)",
}


def setup(uri: str | None = None, force: bool = False) -> bool:
    """Turn tracing on (once). uri/force are for tests: another store, on even when WA_TRACING=0."""
    global _enabled
    if _enabled and not uri:
        return True
    if not (settings.tracing or force):
        return False
    try:
        import os

        os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")  # keep worker logs clean
        import mlflow

        mlflow.set_tracking_uri(uri or settings.mlflow_uri)
        mlflow.set_experiment("web-agent")
        # No autolog: LangGraph's autolog logged the whole agent state at every node (up to 37k characters, the
        # same text again and again) and Groq's logged every call as an anonymous "Completions" with the images'
        # base64. The spans below say which part sent what to which.
        for name in ("langchain", "groq"):
            try:
                getattr(mlflow, name).autolog(disable=True)
            except Exception:
                pass
        _enabled = True
    except Exception as exc:
        log.warning("MLflow tracing is off: %s", exc)
    return _enabled


def enabled() -> bool:
    return _enabled


# ---------------------------------------------------------------- making the data readable

def clip(value: Any, limit: int = 1500) -> Any:
    """A JSON-able copy with long texts cut (the full prompts are in the model spans)."""
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + f" ...[+{len(value) - limit} chars]"
    if isinstance(value, dict):
        return {str(k): clip(v, limit) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clip(v, limit) for v in value[:60]]
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if hasattr(value, "model_dump"):
        return clip(value.model_dump(), limit)
    return clip(str(value), limit)


def messages_view(messages: list[dict], limit: int = 60000) -> list[dict]:
    """The chat messages as sent, images replaced by their size (base64 would fill the store)."""
    out = []
    for m in messages or []:
        content = m.get("content")
        if isinstance(content, list):
            parts = []
            for p in content:
                if p.get("type") == "image_url":
                    url = str((p.get("image_url") or {}).get("url") or "")
                    parts.append({"type": "text", "text": f"[image, {len(url) * 3 // 4 // 1024} KB]"})
                else:
                    parts.append({"type": "text", "text": clip(str(p.get("text") or ""), limit)})
            content = parts
        else:
            content = clip(str(content or ""), limit)
        msg = {"role": m.get("role", "user"), "content": content}
        if m.get("tool_calls"):
            msg["tool_calls"] = clip(m["tool_calls"], 2000)
        out.append(msg)
    return out


def _step_line(s: dict, limit: int = 400) -> str:
    args = json.dumps(s.get("args") or {}, ensure_ascii=False)[:200]
    res = str(s.get("result") or "").replace("\n", " / ")[:limit]
    return f"{s.get('n')}. {s.get('tool')} {args} -> {'' if s.get('ok', True) else 'ERROR: '}{res}"


def _node_inputs(name: str, state: dict) -> dict:
    """What a node received: the parts of the state it works from, not the whole state."""
    steps = state.get("steps") or []
    view: dict[str, Any] = {"task": state.get("task"), "step": state.get("step_count", 0)}
    if name in ("plan", "recall") and state.get("history"):
        view["earlier in this chat"] = clip(state["history"], 600)
    if name in ("decide", "reflect", "check", "learn") and steps:
        view["last steps"] = [_step_line(s) for s in steps[-3:]]
    if state.get("feedback"):
        view["feedback"] = clip(state["feedback"], 800)
    if name in ("gate", "execute") and state.get("pending"):
        view["tool call"] = clip({k: state["pending"].get(k) for k in ("name", "args", "url")}, 800)
    if name == "execute" and state.get("decision"):
        view["gate decision"] = clip(state["decision"], 600)
    if name in ("check", "learn", "give_up") and state.get("answer"):
        view["answer"] = clip(state["answer"], 2000)
    return {k: v for k, v in view.items() if v not in (None, "", [])}


def _node_outputs(state: dict, delta: dict) -> dict:
    """What a node sent on: its update, with only the steps it added."""
    if not isinstance(delta, dict):
        return {"value": clip(delta)}
    out: dict[str, Any] = {}
    before = state.get("step_count", 0)
    for k, v in delta.items():
        if k == "steps":
            new = [s for s in v or [] if (s.get("n") or 0) > before]
            if new:
                out["new steps"] = [_step_line(s, 1200) for s in new]
        elif k in ("lessons", "plan", "answer", "feedback", "decision", "pending", "status", "notes", "sources"):
            out[k] = clip(v, 2000)
        elif k not in ("started", "domains", "tool_groups", "current_url", "checks", "reflections"):
            out[k] = clip(v, 300)
    return out


def _says(name: str, state: dict, delta: dict) -> str:
    """The node's message in one line, as one part of the agent talking to the next."""
    d = delta if isinstance(delta, dict) else {}
    new = [s for s in d.get("steps") or [] if (s.get("n") or 0) > state.get("step_count", 0)]
    last = new[-1] if new else None
    if name == "recall":
        return f"found {len(d.get('lessons') or [])} lessons from earlier tasks"
    if name == "plan":
        return "plan: " + " | ".join(str(p) for p in d.get("plan") or [])[:400]
    if name == "decide":
        p = d.get("pending")
        if p:
            return f"asks the gate to run {p.get('name')} {json.dumps(p.get('args') or {}, ensure_ascii=False)[:250]}"
        if last:
            return f"{last.get('tool')}: {str(last.get('result') or '')[:250]}"
    if name == "gate":
        dec = d.get("decision") or {}
        p = d.get("pending") or state.get("pending") or {}
        if dec.get("refused"):
            return "refused, back to the brain: " + str(dec["refused"])[:300]
        if "reply" in dec:   # the gate asked the person a question (ask_user)
            return "the person answered: " + str(dec["reply"])[:250]
        if dec.get("key"):   # an action that needs the person's OK
            if str(dec.get("note") or "").startswith("approved at step"):
                return f"approved earlier in this chat: run {p.get('name')} ({dec.get('what', '')})"[:300]
            if dec.get("approved"):
                return f"the person approved: run {p.get('name')} ({dec.get('what', '')})"[:300]
            return f"the person said no: {dec.get('what', '')}"[:300]
        return f"allowed (no approval needed): run {p.get('name')}"
    if name in ("execute", "check", "reflect", "learn", "give_up", "stop_rejected") and last:
        return f"{last.get('tool')}: {str(last.get('result') or '')[:300]}".replace("\n", " / ")
    if d.get("answer"):
        return "answer: " + str(d["answer"])[:300]
    return ", ".join(sorted(d)) or "nothing"


# ---------------------------------------------------------------- spans

class _Handle:
    """Safe access to a live span (a no-op when tracing is off)."""

    def __init__(self, span=None):
        self.span = span

    def inputs(self, value) -> None:
        if self.span is not None:
            try:
                self.span.set_inputs(value)
            except Exception as exc:
                log.debug("span inputs: %s", exc)

    def out(self, value) -> None:
        if self.span is not None:
            try:
                self.span.set_outputs(value)
            except Exception as exc:
                log.debug("span outputs: %s", exc)

    def attrs(self, **values) -> None:
        if self.span is not None:
            try:
                self.span.set_attributes({k: v for k, v in values.items() if v is not None})
            except Exception as exc:
                log.debug("span attributes: %s", exc)


@contextlib.contextmanager
def span(name: str, kind: str = "UNKNOWN", inputs: Any = None, who: str | None = None, attrs: dict | None = None):
    """A span under the current one. `who` names the part talking inside it (for the model calls it makes)."""
    token = _who.set(who) if who is not None else None
    try:
        if not _enabled:
            yield _Handle()
            return
        import mlflow

        try:
            cm = mlflow.start_span(name=name, span_type=kind)
        except Exception as exc:
            log.warning("MLflow span failed: %s", exc)
            yield _Handle()
            return
        with cm as s:
            h = _Handle(s)
            if inputs is not None:
                h.inputs(inputs)
            if attrs:
                h.attrs(**attrs)
            yield h
    finally:
        if token is not None:
            _who.reset(token)


def who() -> str:
    return _who.get()


def _safe(fn: Callable, *args) -> Any:
    """A summary for a span, or a note why it failed: never an error in the agent."""
    try:
        return fn(*args)
    except Exception as exc:
        return f"(could not summarise: {type(exc).__name__})"


def _is_pause(exc: BaseException) -> bool:
    """LangGraph's interrupt(): the gate waits for the person. Not an error."""
    return any(c.__name__ in ("GraphInterrupt", "NodeInterrupt", "GraphBubbleUp") for c in type(exc).__mro__)


def node(name: str, fn: Callable) -> Callable:
    """Wrap a LangGraph node: a span with what it received and what it sent on."""
    role = ROLES.get(name, name)

    @functools.wraps(fn)
    def run(state, config):
        if not _enabled:
            return fn(state, config)
        paused = None
        with span(f"{name} · {role}", "AGENT", inputs=_safe(_node_inputs, name, state), who=name,
                  attrs={"agent.part": name, "agent.role": role, "agent.step": state.get("step_count", 0)}) as h:
            try:
                delta = fn(state, config)
            except Exception as exc:
                if not _is_pause(exc):
                    raise
                paused = exc
                p = state.get("pending") or {}
                h.out({"waiting for the person": True, "tool call": clip(p.get("name"))})
                h.attrs(says=f"asks the person before running {p.get('name')}")
            else:
                h.out(_safe(_node_outputs, state, delta))
                h.attrs(says=_safe(_says, name, state, delta or {}))
        if paused is not None:
            raise paused
        return delta

    return run


def route(source: str, fn: Callable) -> Callable:
    """Wrap a LangGraph router: a short span 'route: decide -> gate' for each hand-over."""

    @functools.wraps(fn)
    def pick(state):
        dest = fn(state)
        if _enabled:
            try:
                with span(f"route: {source} -> {dest}", "CHAIN", attrs={"from": source, "to": str(dest)}) as h:
                    h.out({"next": dest})
            except Exception as exc:
                log.warning("MLflow route span failed: %s", exc)
        return dest

    return pick


def person(asked: dict, answer: Any) -> None:
    """The person's answer to a question or approval, as a message into the agent."""
    if not _enabled:
        return
    try:
        kind = (asked or {}).get("kind", "question")
        with span("person -> agent", "UNKNOWN", inputs={"the agent asked": clip(asked, 1500)},
                  attrs={"from": "person", "to": "gate", "kind": kind}) as h:
            h.out({"answer": clip(answer, 1500)})
            h.attrs(says=f"{kind}: {json.dumps(clip(answer, 200), ensure_ascii=False)[:250]}")
    except Exception as exc:  # a span is never worth a task
        log.warning("MLflow person span failed: %s", exc)


@contextlib.contextmanager
def task_trace(task_id: str, request: str):
    """One top-level trace per task, tagged with the task id so it can be found in the MLflow UI."""
    if not setup():
        yield _Handle()
        return
    import mlflow

    with span(f"task {task_id}", "AGENT", inputs={"request": request}, attrs={"task_id": task_id}) as h:
        try:
            mlflow.update_current_trace(tags={"task_id": task_id}, request_preview=request[:1000])
        except Exception:
            pass
        yield h


def task_done(handle: _Handle, result: dict) -> None:
    """The task's outcome on its trace: status, answer, steps and tokens."""
    if not _enabled or handle.span is None:
        return
    try:
        status = str(result.get("status") or "")
        answer = str(result.get("answer") or "")
        handle.out({"status": status, "answer": answer, "steps": result.get("step_count"),
                    "sources": clip(result.get("sources"))})
        import mlflow

        mlflow.update_current_trace(tags={"status": status, "steps": str(result.get("step_count") or 0)},
                                    response_preview=answer[:1000])
    except Exception:
        pass
