"""Capability evaluation: hard kinds of web pages, checked on the RESULT in the page, not only the answer.

  .venv\\Scripts\\python scripts\\eval_lab.py                    # all tasks in evals/lab_tasks.json
  .venv\\Scripts\\python scripts\\eval_lab.py --only sheets-new,booking --label after

Needs the API and the worker running. It serves the lab pages (tests/fixtures/lab) on port 8766; each page reports
what really happened (cells typed, form sent, card moved) to /__state/<name>, and the checks below read it.
Writes data/eval/lab-<time>-<label>.md and .json.
"""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import re
import socketserver
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval import PRICE, ROOT, call, ensure_test_shop  # noqa: E402
import metrics as M  # noqa: E402

LAB = ROOT / "tests" / "fixtures" / "lab"
STATE: dict[str, dict] = {}
RUN = {"id": "0"}


class LabHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(LAB), **k)

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/__run"):   # pages that keep state in the browser start fresh on every task
            body = RUN["id"].encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_POST(self):
        if self.path.startswith("/__state/"):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                STATE[self.path.split("/")[-1]] = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                pass
            self.send_response(204)
            self.end_headers()
            return
        self.send_error(404)

    def send_error(self, code, message=None, explain=None):
        if code == 404:  # a real "page not found" page with a link home, like a real website
            body = (LAB / "404.html").read_bytes()
            self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().send_error(code, message, explain)


def start_lab(port=8766):
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", port), LabHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ---------------------------------------------------------------- result checks (what really happened)

def check_sheets(s):
    cells = (s.get("sheets") or {}).get("cells")
    if not cells:
        return ["no spreadsheet was created or nothing was typed"]
    out = []
    flat = [v for row in cells for v in row]
    if any("\t" in v or "\n" in v for v in flat):
        out.append("several values ended up in one cell")
    head = [v.strip().lower() for v in cells[0][:6]]
    for word in ("date", "product", "units", "price"):
        if not any(word in h for h in head):
            out.append(f"header row lacks '{word}' in its own cell (row 1 = {cells[0][:5]})")
            break
    data = [row for row in cells[1:] if sum(1 for v in row if v.strip()) >= 4]
    if len(data) < 3:
        out.append(f"only {len(data)} data rows with 4 filled cells")
    return out


def check_booking(s):
    b = s.get("booking")
    if not b:
        return ["the booking was not sent"]
    want = {"name": "lina", "date": "2026-12-05", "people": "4 guests", "time": "19:30"}
    return [f"{k} is {b.get(k)!r}, expected {v!r}" for k, v in want.items() if str(b.get(k, "")).strip().lower() != v]


def check_newsletter(s):
    e = (s.get("newsletter") or {}).get("email", "")
    return [] if e.lower() == "omar@example.com" else [f"newsletter email is {e!r}"]


def check_wishlist(s):
    items = (s.get("wishlist") or {}).get("items") or []
    return [] if "Trail Backpack 30L" in items else [f"wishlist is {items}"]


def check_docs(s):
    d = s.get("docs")
    if not d:
        return ["the document was not saved"]
    out = []
    if d.get("title", "").strip().lower() != "weekly plan":
        out.append(f"title is {d.get('title')!r}")
    lines = [x for x in (d.get("text") or "").splitlines() if x.strip()]
    if len(lines) < 3:
        out.append(f"{len(lines)} lines in the body: {d.get('text', '')[:80]!r}")
    return out


def check_cart(s):
    c = s.get("cart") or {}
    out = []
    if (c.get("cart") or {}) != {"USB-C Cable": 2, "Fast Charger": 1}:
        out.append(f"cart is {c.get('cart')}")
    if c.get("coupon") != "SAVE10":
        out.append("coupon not applied")
    if s.get("checkout"):
        out.append("broke rule: it checked out although told not to")
    return out


def check_board(s):
    b = s.get("board") or {}
    return [] if "Write report" in (b.get("Done") or []) else [f"Done column is {b.get('Done')}"]


def check_settings(s):
    st = s.get("settings")
    if not st:
        return ["settings were not saved"]
    out = []
    if st.get("brightness") != 70:
        out.append(f"brightness {st.get('brightness')}")
    if st.get("dark") is not True:
        out.append("dark mode is off")
    return out


MEGA_OK = {"lenovo-ideapad-slim-5", "lenovo-loq-15", "lenovo-thinkpad-t14-renewed"}   # Lenovo, < $800, 16 GB+
MEGA_WORDS = {"lenovo-ideapad-slim-5": "slim 5", "lenovo-loq-15": "loq", "lenovo-thinkpad-t14-renewed": "t14",
              "lenovo-ideapad-3": "ideapad 3", "lenovo-thinkpad-e14": "e14", "lenovo-v15-g4": "v15",
              "lenovo-yoga-7i": "yoga", "lenovo-ideapad-1": "ideapad 1", "lenovo-legion-5": "legion",
              "lenovo-chromebook-duet": "duet", "hp-pavilion-15": "pavilion", "dell-inspiron-14": "inspiron"}


def check_mega(s):
    m = s.get("mega") or {}
    cart = m.get("cart") or []
    out = []
    if not cart:
        out.append("nothing was added to the cart")
    bad = [c for c in cart if c not in MEGA_OK]
    if bad:
        out.append(f"broke rule: the cart has items that do not meet the price/RAM/brand rules: {bad}")
    if len(cart) > 1:
        out.append(f"broke rule: {len(cart)} items in the cart, the task asked for one")
    if m.get("checkout"):
        out.append("broke rule: it went to checkout although told not to")
    return out


def check_mega_nocart(s):
    cart = (s.get("mega") or {}).get("cart") or []
    out = [f"broke rule: it added {cart} to the cart although told not to"] if cart else []
    if (s.get("mega") or {}).get("checkout"):
        out.append("broke rule: it went to checkout")
    return out


CHECKS = {"mega": check_mega, "mega_nocart": check_mega_nocart, "sheets": check_sheets, "booking": check_booking, "newsletter": check_newsletter, "wishlist": check_wishlist,
          "docs": check_docs, "cart": check_cart, "board": check_board, "settings": check_settings}


def run_one(spec, timeout=420):
    STATE.clear()
    RUN["id"] = str(time.time())
    t0 = time.time()
    t = call("POST", "/api/tasks", {"request": spec["request"]})
    approvals = 0
    while time.time() - t0 < timeout:
        d = call("GET", f"/api/tasks/{t['id']}")
        p = d.get("pending")
        if p:
            if p["kind"] == "approval":
                approvals += 1
                call("POST", f"/api/tasks/{t['id']}/approve", {"approved": spec.get("approval") == "approve"})
            else:
                login = (p.get("action") or {}).get("tool") == "login"
                call("POST", f"/api/tasks/{t['id']}/reply",
                     {"text": "skip" if login else spec.get("reply", "I cannot answer now. Please decide yourself.")})
        if d["status"] in ("done", "failed", "cancelled"):
            break
        time.sleep(1.5)
    else:
        call("POST", f"/api/tasks/{t['id']}/cancel")
        time.sleep(3)
        d = call("GET", f"/api/tasks/{t['id']}")
    time.sleep(0.5)
    answer = (d.get("answer") or "").replace(" ", " ").replace(" ", " ")   # narrow no-break spaces
    low = answer.lower()
    asked = sum(1 for s in d["steps"] if s["tool"] == "ask_user" and str(s.get("result") or "").startswith("The person"))
    problems = []
    if d["status"] != "done":
        problems.append(f"status {d['status']}")
    if spec.get("expect_all") and not all(w.lower() in low for w in spec["expect_all"]):
        problems.append(f"answer lacks {spec['expect_all']}")
    if spec.get("expect_any") and not any(w.lower() in low for w in spec["expect_any"]):
        problems.append(f"answer has none of {spec['expect_any']}")
    if spec.get("expect_regex") and not re.search(spec["expect_regex"], answer):
        problems.append("answer does not have the expected fact")
    if spec.get("check"):
        problems += CHECKS[spec["check"]](STATE)
    if spec.get("check") == "mega":
        # the right item in the cart is not enough: the answer must name it (round 4: right cart, answer
        # "no Lenovo laptop meets the criteria", and it counted as a pass)
        cart = (STATE.get("mega") or {}).get("cart") or []
        if cart and not any(MEGA_WORDS.get(c, "~") in low for c in cart):
            problems.append(f"answer does not name the item it added to the cart ({', '.join(cart)})")
    if spec.get("must_not_ask") and asked:
        problems.append(f"broke rule: asked the person {asked}x although it could act itself")
    if spec.get("must_ask_question") and not asked:
        problems.append("broke rule: did not ask for the missing details")
    logins = sum(1 for s in d["steps"] if s["tool"] == "login")
    tools = {}
    for s in d["steps"]:
        tools[s["tool"]] = tools.get(s["tool"], 0) + 1
    return {"id": spec["id"], "category": spec.get("category", ""), "task_id": d["id"], "passed": not problems,
            "problems": problems, "status": d["status"], "seconds": round(time.time() - t0, 1),
            "steps": d["steps_count"], "tokens": d["tokens"], "cost_usd": round(d["tokens"] * PRICE / 1e6, 4),
            "approvals": approvals, "asked": asked, "logins": logins, "tools": tools, "answer": answer[:300],
            "trace": M.trace_of(d)}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default=str(ROOT / "evals" / "lab_tasks.json"))
    ap.add_argument("--only", help="comma-separated task ids")
    ap.add_argument("--label", default="run")
    a = ap.parse_args()
    specs = json.loads(Path(a.tasks).read_text(encoding="utf-8"))
    if a.only:
        keep = set(a.only.split(","))
        specs = [s for s in specs if s["id"] in keep]
    ensure_test_shop()
    # Lessons the agent learned on the lab site in earlier runs would change this run (eval: "clicking the 16 GB
    # box filters the list" from one run misled the next). Every run starts without them.
    sys.path.insert(0, str(ROOT))
    from webagent.store import db
    print(f"Cleared {db.delete_skills('127.0.0.1')} lessons about the lab site from earlier runs.", flush=True)
    start_lab()
    results = []
    for spec in specs:
        print(f"-> {spec['id']} ({spec['category']})", flush=True)
        r = run_one(spec)
        results.append(r)
        print(f"   {'PASS' if r['passed'] else 'FAIL'} {r['seconds']}s {r['steps']} steps {r['tokens']} tokens"
              f"{'  ' + '; '.join(r['problems']) if r['problems'] else ''}", flush=True)
    n, passed = len(results), sum(r["passed"] for r in results)
    summary = {"when": datetime.now().isoformat(timespec="minutes"), "label": a.label, "tasks": n, "passed": passed,
               "success_rate": round(100 * passed / n, 1) if n else 0,
               "avg_steps": round(sum(r["steps"] for r in results) / n, 1) if n else 0,
               "avg_tokens": round(sum(r["tokens"] for r in results) / n) if n else 0,
               "avg_seconds": round(sum(r["seconds"] for r in results) / n, 1) if n else 0,
               "metrics": M.summarize(results) if n else {}}
    out = ROOT / "data" / "eval"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    (out / f"lab-{stamp}-{a.label}.json").write_text(json.dumps({"summary": summary, "results": results}, indent=2,
                                                                ensure_ascii=False), encoding="utf-8")
    lines = [f"# Capability eval ({a.label}) {summary['when']}", "",
             f"**{passed} of {n} passed ({summary['success_rate']}%)** - average {summary['avg_seconds']} s, "
             f"{summary['avg_steps']} steps, {summary['avg_tokens']:,} tokens.", "",
             "## The 8 metrics", ""] + (M.markdown(summary["metrics"]) if n else []) + [
             "", "## Tasks", "",
             "| Task | Capability | Result | Steps | Tokens | What went wrong |", "| --- | --- | --- | --- | --- | --- |"]
    for r in results:
        lines.append(f"| {r['id']} | {r['category']} | {'PASS' if r['passed'] else 'FAIL'} | {r['steps']} | "
                     f"{r['tokens']:,} | {'; '.join(r['problems']).replace('|', '/') or '-'} |")
    (out / f"lab-{stamp}-{a.label}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + lines[2])
    print(f"Report: {out / f'lab-{stamp}-{a.label}.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
