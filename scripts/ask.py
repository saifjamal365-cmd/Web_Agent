"""Command-line client: send a task to the running Web Agent API and follow it.

  .venv\\Scripts\\python scripts\\ask.py "What is the latest Python version?"
  .venv\\Scripts\\python scripts\\ask.py --conversation abc123 "And when was it released?"

Approvals and questions are answered in the terminal (y/n or text).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("WA_URL", "http://127.0.0.1:8020")
KEY = os.environ.get("WA_API_KEY", "")


def call(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **({"X-API-Key": KEY} if KEY else {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"null")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles are not UTF-8 by default
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("request")
    ap.add_argument("--conversation")
    ap.add_argument("--start-url")
    ap.add_argument("--auto", choices=["approve", "reject"], help="answer approvals automatically (testing)")
    ap.add_argument("--reply", help="answer to give if the agent asks a question (testing)")
    a = ap.parse_args()
    t = call("POST", "/api/tasks", {"request": a.request, "conversation_id": a.conversation, "start_url": a.start_url})
    print(f"task {t['id']}  conversation {t['conversation_id']}", flush=True)
    shown, start = 0, time.time()
    while True:
        d = call("GET", f"/api/tasks/{t['id']}")
        for s in d["steps"][shown:]:
            args = {k: v for k, v in (s.get("args") or {}).items()}
            res = (s.get("result") or "").replace("\n", " ")[:220]
            print(f"  {s['n']:>2}. {s['tool']} {json.dumps(args, ensure_ascii=False)[:120]}\n      why: {s.get('why') or '-'}\n"
                  f"      -> {'' if s['ok'] else 'ERROR '}{res}", flush=True)
        shown = len(d["steps"])
        p = d.get("pending")
        if p and p["kind"] == "approval":
            print(f"\n  APPROVAL NEEDED: {p['message']}\n  {p['action'].get('what')}", flush=True)
            ok = (a.auto == "approve") if a.auto else input("  Approve? [y/N] ").strip().lower() == "y"
            call("POST", f"/api/tasks/{t['id']}/approve", {"approved": ok})
        elif p and p["kind"] == "question":
            print(f"\n  AGENT ASKS: {p['message']}", flush=True)
            text = a.reply or input("  Your answer: ")
            call("POST", f"/api/tasks/{t['id']}/reply", {"text": text})
        if d["status"] in ("done", "failed", "cancelled"):
            print(f"\n[{d['status'].upper()}] after {time.time() - start:.0f}s, {d['steps_count']} steps, {d['tokens']} tokens")
            print(d.get("answer") or d.get("error"))
            if d.get("sources"):
                print("Sources:", ", ".join(d["sources"]))
            return 0 if d["status"] == "done" else 1
        time.sleep(1.5)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.URLError as exc:
        print(f"Cannot reach the API at {BASE}: {exc}")
        sys.exit(2)
