"""Evaluate the agent's recent MLflow traces and detect issues, on Windows.

The MLflow UI's "Run judges" and "Run issue detection" buttons need MLflow's job backend, which does not run on
Windows ("MLflow job backend does not support Windows system"). This script runs the same things in Python and
writes the results to the same store, so they show in the UI:

  Traces tab        a column per check and judge on each trace (pass/fail with the reason)
  Evaluation runs   one run per evaluation, with the pass rate of each check
  Issues            the issues MLflow's detector found, grouped, with example traces

Usage:
  .venv\\Scripts\\python scripts\\eval_traces.py                 # last 20 tasks: checks + judges + issues
  .venv\\Scripts\\python scripts\\eval_traces.py --last 50 --no-issues
  .venv\\Scripts\\python scripts\\eval_traces.py --checks-only   # free: no model calls

Judges and issue detection use Groq (GROQ_API_KEY from the environment) through litellm.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")

import mlflow  # noqa: E402
from mlflow.genai.scorers import Guidelines, RelevanceToQuery  # noqa: E402

from webagent import trace_checks  # noqa: E402
from webagent.config import settings  # noqa: E402

JUDGE_MODEL = "groq:/openai/gpt-oss-120b"

GUIDELINES = [
    "The response answers in the same language as the request (an Arabic request gets an Arabic answer).",
    "When the task could not be fully done, the response says so plainly and gives what was found.",
    "The response does not claim actions it did not do (buying, sending, deleting) and does not ask the user for "
    "something the request already gave.",
]


def recent_traces(experiment_id: str, last: int) -> list:
    """The newest finished agent tasks (older autolog traces and running tasks are skipped)."""
    found = mlflow.search_traces(locations=[experiment_id], max_results=last * 3, return_type="list",
                                 order_by=["timestamp_ms DESC"])
    # A task the person cancelled says nothing about the agent: left out.
    return [t for t in found if trace_checks.is_agent_trace(t)
            and trace_checks._outcome(t).get("status") != "cancelled"][:last]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--last", type=int, default=20, help="how many recent tasks (default 20)")
    ap.add_argument("--checks-only", action="store_true", help="only the free checks: no judges, no issues")
    ap.add_argument("--no-judges", action="store_true", help="skip the Groq judges")
    ap.add_argument("--no-issues", action="store_true", help="skip issue detection")
    ap.add_argument("--model", default=JUDGE_MODEL, help=f"judge model (default {JUDGE_MODEL})")
    args = ap.parse_args()

    mlflow.set_tracking_uri(settings.mlflow_uri)
    exp = mlflow.set_experiment("web-agent")
    traces = recent_traces(exp.experiment_id, args.last)
    if not traces:
        print("No finished agent traces yet: run a task in the dashboard first.")
        return 1
    print(f"{len(traces)} traces, newest {traces[0].info.trace_id}")

    use_model = not args.checks_only
    if use_model and not os.environ.get("GROQ_API_KEY"):
        print("GROQ_API_KEY is not set: running the free checks only.")
        use_model = False

    scorers = list(trace_checks.CHECKS)
    if use_model and not args.no_judges:
        scorers += [RelevanceToQuery(model=args.model),
                    Guidelines(name="answer_rules", guidelines=GUIDELINES, model=args.model)]

    # ---- evaluation: one run, a result per trace and scorer (shown on the traces too)
    result = mlflow.genai.evaluate(data=traces, scorers=scorers)
    print(f"\nEvaluation run {result.run_id}:")
    for name, value in sorted(result.metrics.items()):
        if name.endswith("/mean"):
            print(f"  {name[:-5]:<24} {value:.0%} pass")

    # ---- issue detection: failing traces grouped into issues on the Issues page
    if use_model and not args.no_issues:
        from mlflow.genai.discovery.pipeline import discover_issues

        print("\nDetecting issues (Groq)...")
        found = discover_issues(experiment_id=exp.experiment_id, traces=traces, scorers=list(trace_checks.CHECKS),
                                model=args.model, max_issues=10)
        issues = getattr(found, "issues", None) or []
        print(f"{len(issues)} issues:")
        for issue in issues:
            name = getattr(issue, "name", None) or getattr(issue, "title", "")
            print(f"  - [{getattr(issue, 'severity', '')}] {name}")
    print(f"\nOpen http://127.0.0.1:5000/#/experiments/{exp.experiment_id}/traces (and Evaluation runs, Issues).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
