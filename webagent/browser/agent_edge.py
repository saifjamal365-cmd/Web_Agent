"""The agent's Edge: a normal, visible Edge window with its own profile, that the agent works in.

Why: some sites (Google first of all) refuse to sign in inside a browser that a program started. Here the person
signs in themselves, in an ordinary Edge window, once; the sign-in stays in this profile and the agent then works
in new tabs of that window (Playwright connect_over_cdp on 127.0.0.1). The person's own Edge profile, passwords
and accounts are never used: this profile only has the sites the person signed in to here.

Settings: WA_AGENT_EDGE=1 to use it, WA_AGENT_EDGE_PORT (9333), WA_AGENT_EDGE_PROFILE (default
%LOCALAPPDATA%\\WebAgent\\edge-profile: outside OneDrive, because it holds sign-in cookies).
Note: while it runs, programs on this computer can control this window through the local port.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

import httpx

from webagent.config import settings

EDGE_PATHS = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]


def endpoint() -> str:
    return f"http://127.0.0.1:{settings.agent_edge_port}"


def is_running() -> bool:
    try:
        return httpx.get(endpoint() + "/json/version", timeout=1.5).status_code == 200
    except httpx.HTTPError:
        return False


def edge_path() -> str | None:
    for p in EDGE_PATHS:
        if Path(p).is_file():
            return p
    return shutil.which("msedge")


def start(url: str = "") -> bool:
    """Open the agent's Edge window if it is not open yet. True when it is ready."""
    if is_running():
        return True
    exe = edge_path()
    if not exe:
        return False
    profile = Path(settings.agent_edge_profile)
    profile.mkdir(parents=True, exist_ok=True)
    args = [exe, f"--remote-debugging-port={settings.agent_edge_port}", f"--user-data-dir={profile}",
            "--no-first-run", "--no-default-browser-check", url or "about:blank"]
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    subprocess.Popen(args, creationflags=flags, close_fds=True, env=os.environ.copy())
    for _ in range(40):
        if is_running():
            return True
        time.sleep(0.5)
    return False


def open_tab(url: str) -> bool:
    """Open a new tab in the agent's Edge (starting it if needed) and show it to the person."""
    if not is_running():
        return start(url)
    try:
        r = httpx.put(endpoint() + "/json/new?" + quote(url, safe=":/?&=%#"), timeout=5)
        tab = r.json()
        httpx.get(endpoint() + "/json/activate/" + tab["id"], timeout=5)
        return True
    except (httpx.HTTPError, ValueError, KeyError):
        return False
