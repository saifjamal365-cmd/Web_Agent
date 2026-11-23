"""Open the agent's Edge window, to sign in to websites ahead of time (Google, Gmail, ...).

    .venv\\Scripts\\python scripts\\agent_edge.py https://accounts.google.com

It is a normal Edge window with its own profile (not your own Edge profile). Sign in there yourself; the agent
uses those sign-ins when WA_AGENT_EDGE=1. Keep the window open (or let the agent open it again when a task starts).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from webagent.browser import agent_edge  # noqa: E402
from webagent.config import settings  # noqa: E402


def main(url: str) -> int:
    ok = agent_edge.open_tab(url) if url else agent_edge.start()
    if not ok:
        print("Could not open Microsoft Edge.")
        return 1
    print(f"The agent's Edge is open (profile: {settings.agent_edge_profile}). Sign in to the sites the agent needs.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
