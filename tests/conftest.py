"""Test setup: a temporary database, no tracing, no real model, and a local test website."""
import functools
import json
import http.server
import os
import re
import socketserver
import sys
import tempfile
import threading
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="webagent-test-"))
# TEST_DATABASE_URL=postgresql+psycopg://... runs the same tests on PostgreSQL + pgvector.
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ["WA_TRACING"] = "0"
os.environ["WA_LIVE_DIR"] = str(_TMP / "live")
os.environ["WA_INLINE"] = "1"
os.environ["WA_API_KEY"] = ""
os.environ["WA_APPROVAL_TIMEOUT"] = "20"
os.environ.setdefault("GROQ_API_KEY", "test-key-not-real")
os.environ["WA_HEADLESS"] = "1"
os.environ["WA_FILES_DIR"] = str(_TMP / "files")
os.environ["WA_SESSIONS_DIR"] = str(_TMP / "sessions")
os.environ["WA_DESKTOP"] = "0"  # tests never open apps on the screen (tests/test_desktop.py opts in)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from webagent.llm import Reply, ToolCall  # noqa: E402

SITE = Path(__file__).parent / "fixtures" / "site"


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture(scope="session")
def site_url():
    handler = functools.partial(_Quiet, directory=str(SITE))
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def ref_for(page: str, name: str, role: str | None = None) -> str:
    """Find the ref of an element in a page list by (part of) its name."""
    for line in page.splitlines():
        m = re.match(r'(e\d+) \[([^\]]+)\] "([^"]*)"', line)
        if m and name.lower() in m.group(3).lower() and (role is None or m.group(2) == role):
            return m.group(1)
    raise AssertionError(f"No element named {name!r} in page:\n{page}")


class FakeLLM:
    """Scripted model. `script` is a list of (tool_name, args or callable(page_text) -> args)."""

    def __init__(self, script, check_ok=True, salvage=""):
        self.script = list(script)
        self.check_ok = check_ok
        self.salvage = salvage  # the answer written from the notes when the agent gives up
        self.calls = []

    def chat(self, messages, *, tools=None, tool_choice=None, json_mode=False, model=None, max_tokens=1500,
             temperature=0.2, reasoning=None):
        system = messages[0]["content"] if isinstance(messages[0]["content"], str) else ""
        user = messages[-1]["content"] if isinstance(messages[-1]["content"], str) else ""
        self.calls.append({"json": json_mode, "user": user})
        if json_mode:
            if "plan a web task" in system:
                return Reply(text='{"plan": ["Open the shop", "Find the item", "Answer"]}', tokens=10)
            if "check the final answer" in system:
                return Reply(text='{"ok": %s, "problem": "missing price"}' % ("true" if self.check_ok else "false"), tokens=5)
            if "got stuck before it called finish" in system:
                return Reply(text=json.dumps({"answer": self.salvage}), tokens=5)
            if "lessons" in system:
                return Reply(text='{"lessons": [{"domain": "127.0.0.1", "lesson": "Search box is at the top of the home page."}]}', tokens=5)
            return Reply(text='{"problem": "stuck", "plan": ["Try another way"]}', tokens=5)
        if not self.script:
            return Reply(tool_calls=[ToolCall("give_up", {"reason": "script ended", "why": "test"})])
        name, args = self.script.pop(0)
        page = user.split("CURRENT PAGE:", 1)[-1]
        if callable(args):
            args = args(page)
        return Reply(tool_calls=[ToolCall(name, {**args, "why": args.get("why", "test step")})], tokens=100)
