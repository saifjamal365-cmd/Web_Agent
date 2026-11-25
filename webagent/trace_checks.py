"""Issue checks for the agent's MLflow traces (see webagent/tracing.py for what a trace holds).

Each check reads one task's trace and says pass (True) or fail (False) with the reason. They cost nothing (no
model call), so they can run on every trace; scripts/eval_traces.py runs them with MLflow's evaluate and issue
detection, and the results show in the MLflow UI (Traces columns, Evaluation runs, Issues).

The checks are written from failures seen live: the 40-step Petra run that clicked the photo viewer instead of
using look, answers the checker rejected, Groq's invalid tool calls, refusal storms at the gate.
"""
from __future__ import annotations

import json
import re
from collections import Counter

from mlflow.entities import Feedback
from mlflow.genai.scorers import scorer

from webagent.config import settings

MAX_TOKENS = 150_000      # a normal task uses 20k-70k; Amazon-sized ones about 300k
MAX_SECONDS = 300
PICTURE_WORDS = re.compile(r"\b(photo|picture|image|screenshot|chart|graph|logo|colou?r|looks? like|shown? in)\b|"
                           r"صور|الصورة|رسم بياني|لون", re.I)


# ---------------------------------------------------------------- reading a trace

def _root(trace):
    return next((s for s in trace.data.spans if s.parent_id is None), None)


def _outcome(trace) -> dict:
    root = _root(trace)
    out = root.outputs if root is not None else None
    return out if isinstance(out, dict) else {}


def _request(trace) -> str:
    root = _root(trace)
    inp = root.inputs if root is not None else None
    return str((inp or {}).get("request") or "") if isinstance(inp, dict) else str(inp or "")


def _ordered(trace, prefix: str) -> list:
    return sorted((s for s in trace.data.spans if s.name.startswith(prefix)), key=lambda s: s.start_time_ns)


def _says(span) -> str:
    return str((span.attributes or {}).get("says") or "")


def _tools(trace) -> list[tuple[str, str]]:
    """(tool, arguments) of every tool the browser ran, in order."""
    out = []
    for s in _ordered(trace, "tool · "):
        args = {k: v for k, v in (s.inputs or {}).items() if k != "why"} if isinstance(s.inputs, dict) else {}
        out.append((s.name.split("· ", 1)[1], json.dumps(args, sort_keys=True, ensure_ascii=False)))
    return out


def is_agent_trace(trace) -> bool:
    """A finished task traced by webagent/tracing.py (not an older autolog trace or a task still running)."""
    root = _root(trace)
    return bool(root is not None and root.name.startswith("task ") and _outcome(trace).get("status"))


# ---------------------------------------------------------------- the checks (True = fine)

@scorer
def task_finished(trace) -> Feedback:
    status = _outcome(trace).get("status")
    return Feedback(value=status == "done", rationale=f"The task ended as '{status}'."
                    + ("" if status == "done" else f" Answer: {str(_outcome(trace).get('answer') or '')[:300]}"))


@scorer
def within_step_budget(trace) -> Feedback:
    out = _outcome(trace)
    steps = int(out.get("steps") or 0)
    ran_out = steps >= settings.max_steps or str(out.get("answer") or "").startswith("Stopped after")
    return Feedback(value=not ran_out, rationale=f"{steps} steps of {settings.max_steps}."
                    + (" It ran out of steps before finishing." if ran_out else ""))


@scorer
def no_loops(trace) -> Feedback:
    """The same action again and again, or going to a page and back over and over."""
    tools = _tools(trace)
    repeated = [(t, a) for (t, a), n in Counter(tools).items() if n >= 3 and t not in ("scroll", "back", "note")]
    backs = sum(1 for t, _ in tools if t == "back")
    problems = [f"{t} {a[:120]} ran {Counter(tools)[(t, a)]} times" for t, a in repeated]
    if backs >= 3:
        problems.append(f"went back {backs} times (opening pages and leaving them again)")
    return Feedback(value=not problems, rationale="; ".join(problems) or f"{len(tools)} tool calls, no loop.")


@scorer
def few_refusals(trace) -> Feedback:
    refused = [_says(s) for s in _ordered(trace, "gate · ") if _says(s).startswith("refused")]
    return Feedback(value=len(refused) <= 2, rationale=f"The safety gate refused {len(refused)} actions."
                    + (" " + " | ".join(r[:160] for r in refused[:4]) if refused else ""))


@scorer
def checker_accepted_first(trace) -> Feedback:
    """The checker rejected a finished answer (often a made-up or unsupported detail)."""
    rejected = [_says(s) for s in _ordered(trace, "check · ") if "Not OK" in _says(s)]
    return Feedback(value=not rejected, rationale=" | ".join(r[:300] for r in rejected)
                    or "The checker accepted the first answer.")


@scorer
def valid_tool_calls(trace) -> Feedback:
    bad = [_says(s) for s in _ordered(trace, "decide · ") if "invalid tool call" in _says(s).lower()]
    return Feedback(value=not bad, rationale=f"{len(bad)} invalid tool calls from the model."
                    + (" " + bad[0][:200] if bad else ""))


@scorer
def no_errors(trace) -> Feedback:
    errors = [s.name for s in trace.data.spans if str(getattr(s.status, "status_code", "")).endswith("ERROR")]
    return Feedback(value=not errors, rationale=("Errors in: " + ", ".join(errors[:5])) if errors else "No span failed.")


@scorer
def token_budget(trace) -> Feedback:
    used = sum(int((s.attributes or {}).get("tokens") or 0) for s in trace.data.spans if s.name.startswith("LLM "))
    return Feedback(value=used <= MAX_TOKENS, rationale=f"{used:,} tokens (budget {MAX_TOKENS:,}).")


@scorer
def fast_enough(trace) -> Feedback:
    seconds = (trace.info.execution_duration or 0) / 1000
    return Feedback(value=seconds <= MAX_SECONDS, rationale=f"{seconds:.0f} s (limit {MAX_SECONDS} s).")


@scorer
def looked_at_pictures(trace) -> Feedback:
    """A question about what a picture shows needs the vision model (live: the Petra photo, 40 steps of clicking)."""
    if not PICTURE_WORDS.search(_request(trace)):
        return Feedback(value=True, rationale="The task is not about a picture.")
    looks = [a for t, a in _tools(trace) if t == "look"]
    return Feedback(value=bool(looks), rationale=f"The task is about a picture; look ran {len(looks)} times."
                    + ("" if looks else " The agent never used vision."))


CHECKS = [task_finished, within_step_budget, no_loops, few_refusals, checker_accepted_first, valid_tool_calls,
          no_errors, token_budget, fast_enough, looked_at_pictures]
