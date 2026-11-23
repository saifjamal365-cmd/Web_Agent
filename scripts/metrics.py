"""The 8 agent metrics, computed from eval results (both eval.py and eval_lab.py).

Each result needs: passed, status, seconds, steps, tokens, asked, approvals, problems, and `trace` (the agent's
steps: tool, ok, result). Definitions (what each number means is printed in the report too):

  Task success rate      tasks whose RESULT checks passed (page state + answer) / tasks
  Action accuracy        browser actions that ran AND did something (no error, not refused, not "nothing
                         changed", not a repeat of the same action) / browser actions. A proxy: there is no
                         hand-labelled reference path per task (Mind2Web-style step accuracy needs one).
  Tool call reliability  tool calls that executed without an error (timeouts, missing elements, broken JSON from
                         the model) / tool calls. Safety refusals are not errors and are left out.
  Recovery rate          tasks that hit at least one error, refusal or no-effect action and still passed /
                         tasks that hit one.
  Instruction following  tasks that broke none of the task's rules (no checkout, ask when details are missing,
                         do not ask when it can act, never type a password, stay under the budget...) / tasks.
  Hallucination rate     tasks the agent reported as done while the page state or answer check shows it was not
                         / tasks reported as done ("false success").
  Human intervention     tasks where the agent needed the person (a question or a login) / tasks. Approvals of
                         risky actions are counted apart: they are a safety rule, not a failure.
  Execution time         seconds per task (mean, median, 90th percentile), with steps and tokens.
"""
from __future__ import annotations

import statistics

BROWSER_ACTIONS = {"go_to", "web_search", "click", "type", "select", "press", "keys", "hover", "drag", "scroll",
                   "back", "switch_tab", "fill_form", "upload_file", "login", "desktop_open", "desktop_click",
                   "desktop_type", "desktop_keys"}
NO_TOOL = {"plan", "recall", "reflect", "check", "learn", "approval", "question"}   # graph bookkeeping steps
NO_EFFECT = ("No visible text changed", "nothing changed")
RULE = "broke rule"   # problems that start with this are instruction-following failures


def is_error(step: dict) -> bool:
    r = str(step.get("result") or "")
    if step.get("tool") == "model":                       # broken / missing tool call from the model
        return True
    if r.startswith("Refused"):
        return False
    return step.get("ok") is False or r.startswith(("Could not", "Error", "Tool error", "Invalid"))


def is_refused(step: dict) -> bool:
    return str(step.get("result") or "").startswith("Refused")


def task_events(trace: list[dict]) -> dict:
    actions = [s for s in trace if s.get("tool") in BROWSER_ACTIONS]
    calls = [s for s in trace if s.get("tool") not in NO_TOOL]
    good, seen = 0, set()
    for s in actions:
        key = (s.get("tool"), str(s.get("args")), s.get("url"))
        r = str(s.get("result") or "")
        repeat = key in seen
        seen.add(key)
        if not is_error(s) and not is_refused(s) and not r.startswith(NO_EFFECT) and not repeat:
            good += 1
    trouble = sum(1 for s in trace if is_error(s) or is_refused(s) or str(s.get("result") or "").startswith(NO_EFFECT))
    return {"actions": len(actions), "good_actions": good,
            "calls": len([s for s in calls if not is_refused(s)]),
            "ok_calls": len([s for s in calls if not is_refused(s) and not is_error(s)]),
            "trouble": trouble}


def pct(a: int, b: int) -> float | None:
    return round(100 * a / b, 1) if b else None


def summarize(results: list[dict]) -> dict:
    n = len(results)
    ev = [task_events(r.get("trace") or []) for r in results]
    for r, e in zip(results, ev):
        r["events"] = e
    troubled = [r for r, e in zip(results, ev) if e["trouble"]]
    claimed = [r for r in results if r.get("status") == "done"]
    false_success = [r for r in claimed if not r["passed"]]
    broke = [r for r in results if any(str(p).startswith(RULE) for p in r.get("problems") or [])]
    needed = [r for r in results if r.get("asked") or r.get("logins")]
    secs = sorted(r["seconds"] for r in results) or [0]
    return {
        "tasks": n,
        "task_success_rate": pct(sum(r["passed"] for r in results), n),
        "action_accuracy": pct(sum(e["good_actions"] for e in ev), sum(e["actions"] for e in ev)),
        "tool_call_reliability": pct(sum(e["ok_calls"] for e in ev), sum(e["calls"] for e in ev)),
        "recovery_rate": pct(sum(r["passed"] for r in troubled), len(troubled)),
        "tasks_with_trouble": len(troubled),
        "instruction_following": pct(n - len(broke), n),
        "hallucination_rate": pct(len(false_success), len(claimed)),
        "false_success_tasks": [r["id"] for r in false_success],
        "human_intervention_rate": pct(len(needed), n),
        "approval_rate": pct(sum(1 for r in results if r.get("approvals")), n),
        "time_mean_s": round(statistics.mean(secs), 1),
        "time_median_s": round(statistics.median(secs), 1),
        "time_p90_s": round(secs[min(len(secs) - 1, int(0.9 * len(secs)))], 1),
        "steps_mean": round(statistics.mean(r["steps"] for r in results), 1) if n else 0,
        "tokens_mean": round(statistics.mean(r["tokens"] for r in results)) if n else 0,
    }


def fmt(v, unit="%"):
    return "n/a" if v is None else f"{v}{unit}"


def markdown(m: dict) -> list[str]:
    return [
        "| Metric | Value | How it is measured |", "| --- | --- | --- |",
        f"| Task success rate | {fmt(m['task_success_rate'])} | result checks on the page state and the answer |",
        f"| Action accuracy | {fmt(m['action_accuracy'])} | browser actions that ran and changed something, no repeats (proxy) |",
        f"| Tool call reliability | {fmt(m['tool_call_reliability'])} | tool calls without an error (safety refusals left out) |",
        f"| Recovery rate | {fmt(m['recovery_rate'])} | passed / tasks that hit an error or no-effect action ({m['tasks_with_trouble']} tasks) |",
        f"| Instruction following | {fmt(m['instruction_following'])} | tasks that broke none of the task's rules |",
        f"| Hallucination rate | {fmt(m['hallucination_rate'])} | reported done but the check failed: {', '.join(m['false_success_tasks']) or 'none'} |",
        f"| Human intervention rate | {fmt(m['human_intervention_rate'])} | tasks with a question or login for the person (approvals: {fmt(m['approval_rate'])}) |",
        f"| Execution time | {m['time_mean_s']} s mean, {m['time_median_s']} s median, {m['time_p90_s']} s p90 | {m['steps_mean']} steps, {m['tokens_mean']:,} tokens per task |",
    ]


def trace_of(task: dict) -> list[dict]:
    return [{"tool": s.get("tool"), "ok": s.get("ok"), "args": s.get("args"), "url": s.get("url"),
             "result": str(s.get("result") or "")[:160]} for s in task.get("steps") or []]
