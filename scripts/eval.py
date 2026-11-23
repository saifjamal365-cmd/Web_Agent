"""Evaluation: run a set of tasks against the running API and measure the pilot targets.

  .venv\\Scripts\\python scripts\\eval.py                      # all tasks in evals/tasks.json
  .venv\\Scripts\\python scripts\\eval.py --only book-price,jordan-un

A task passes when it ends "done", its answer has the expected words, the agent asked for approval when it had to,
and it never typed forbidden text. Writes data/eval/report-<time>.md and .json.
Cost is an estimate: tokens x WA_COST_PER_MTOK (US$ per million tokens, default 0.20 for gpt-oss-120b on Groq).
"""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import socket
import socketserver
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = os.environ.get("WA_URL", "http://127.0.0.1:8020")
KEY = os.environ.get("WA_API_KEY", "")
PRICE = float(os.environ.get("WA_COST_PER_MTOK", "0.20"))


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **({"X-API-Key": KEY} if KEY else {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8") or "null")


def ensure_test_shop(port=8765):
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", port)) == 0:
            return
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT / "tests" / "fixtures" / "site"))
    handler.log_message = lambda *a: None
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def _metrics():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import metrics
    return metrics


def run_one(spec, timeout=600):
    t0 = time.time()
    t = call("POST", "/api/tasks", {"request": spec["request"]})
    asked = False
    while time.time() - t0 < timeout:
        d = call("GET", f"/api/tasks/{t['id']}")
        p = d.get("pending")
        if p:
            asked = asked or p["kind"] == "approval"
            if p["kind"] == "approval":
                call("POST", f"/api/tasks/{t['id']}/approve", {"approved": spec.get("approval") == "approve"})
            else:
                call("POST", f"/api/tasks/{t['id']}/reply", {"text": spec.get("reply", "Please decide yourself.")})
        if d["status"] in ("done", "failed", "cancelled"):
            break
        time.sleep(1.5)
    else:
        call("POST", f"/api/tasks/{t['id']}/cancel")
        d = call("GET", f"/api/tasks/{t['id']}")
    # models write "6:00 pm" (narrow no-break space): the checks compare plain spaces
    answer = (d.get("answer") or "").replace(" ", " ").replace(" ", " ")
    low = answer.lower()
    problems = []
    if d["status"] != "done":
        problems.append(f"status {d['status']}")
    if spec.get("expect_all") and not all(w.lower() in low for w in spec["expect_all"]):
        problems.append(f"answer lacks {spec['expect_all']}")
    if spec.get("expect_any") and not any(w.lower() in low for w in spec["expect_any"]):
        problems.append(f"answer has none of {spec['expect_any']}")
    if spec.get("must_ask") and not asked:
        problems.append("broke rule: did not ask for approval")
    typed = [s for s in d["steps"] if s["tool"] == "type" and s["ok"]]
    for bad in spec.get("must_not_type", []):
        if any(bad in json.dumps(s["args"]) for s in typed):
            problems.append(f"broke rule: typed forbidden text {bad!r}")
    return {"id": spec["id"], "category": spec.get("category", ""), "task_id": d["id"], "passed": not problems,
            "problems": problems, "status": d["status"], "seconds": round(time.time() - t0, 1),
            "steps": d["steps_count"], "tokens": d["tokens"], "cost_usd": round(d["tokens"] * PRICE / 1e6, 4),
            "asked_approval": asked, "approvals": int(asked), "answer": answer[:400],
            "asked": sum(1 for s in d["steps"] if s["tool"] == "ask_user"),
            "logins": sum(1 for s in d["steps"] if s["tool"] == "login"), "trace": _metrics().trace_of(d)}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default=str(ROOT / "evals" / "tasks.json"))
    ap.add_argument("--only", help="comma-separated task ids")
    a = ap.parse_args()
    specs = json.loads(Path(a.tasks).read_text(encoding="utf-8"))
    if a.only:
        keep = set(a.only.split(","))
        specs = [s for s in specs if s["id"] in keep]
    ensure_test_shop()
    results = []
    for spec in specs:
        print(f"-> {spec['id']}: {spec['request'][:80]}", flush=True)
        r = run_one(spec)
        results.append(r)
        print(f"   {'PASS' if r['passed'] else 'FAIL'} {r['seconds']}s {r['steps']} steps {r['tokens']} tokens"
              f"{'  ' + '; '.join(r['problems']) if r['problems'] else ''}\n   {r['answer'][:200]}", flush=True)
    n = len(results)
    passed = sum(r["passed"] for r in results)
    summary = {
        "when": datetime.now().isoformat(timespec="minutes"), "tasks": n, "passed": passed,
        "success_rate": round(100 * passed / n, 1) if n else 0,
        "avg_seconds": round(sum(r["seconds"] for r in results) / n, 1) if n else 0,
        "avg_steps": round(sum(r["steps"] for r in results) / n, 1) if n else 0,
        "avg_tokens": round(sum(r["tokens"] for r in results) / n) if n else 0,
        "avg_cost_usd": round(sum(r["cost_usd"] for r in results) / n, 4) if n else 0,
        "unapproved_risky_actions": sum(1 for r in results if "broke rule: did not ask for approval" in r["problems"]),
    }
    summary["metrics"] = _metrics().summarize(results) if n else {}
    out = ROOT / "data" / "eval"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    (out / f"report-{stamp}.json").write_text(json.dumps({"summary": summary, "results": results}, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [f"# Eval report {summary['when']}", "",
             f"**{passed} of {n} passed ({summary['success_rate']}%)** - average {summary['avg_seconds']} s, "
             f"{summary['avg_steps']} steps, {summary['avg_tokens']:,} tokens, about ${summary['avg_cost_usd']} per task (estimate).", "",
             "## The 8 metrics", ""] + (_metrics().markdown(summary["metrics"]) if n else []) + ["", "## Tasks", "",
             "| Task | Category | Result | Time | Steps | Tokens | Notes |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in results:
        lines.append(f"| {r['id']} | {r['category']} | {'PASS' if r['passed'] else 'FAIL'} | {r['seconds']} s | {r['steps']} | "
                     f"{r['tokens']:,} | {'; '.join(r['problems']) or r['answer'][:90].replace('|', '/')} |")
    (out / f"report-{stamp}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines[:3]))
    print(f"Report: {out / f'report-{stamp}.md'}")
    return 0 if passed == n else 1


if __name__ == "__main__":
    sys.exit(main())
