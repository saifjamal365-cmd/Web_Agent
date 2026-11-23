"""Save your own login for a website, for the agent to use later. You log in yourself; the agent never sees
your password. Only that website's cookies are kept, in data/sessions/<site>.json.

    .venv\\Scripts\\python scripts\\login.py https://github.com/login

A browser window opens (a separate, empty browser profile of the same browser the agent uses, so your own
browser's passwords and accounts are not touched). Log in as usual, then close the window. A login is only saved
when you really signed in: a sign-in form was used (or the site set new private cookies) and the last page no
longer asks for a password. The dashboard reads the result from data/login_status/<site>.json.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from webagent.agent.safety import domain_of  # noqa: E402
from webagent.browser.session import _domain_match  # noqa: E402
from webagent.config import settings  # noqa: E402
from webagent.files import DATA, SESSIONS, safe_name  # noqa: E402

STATUS = DATA / "login_status"
MAX_OPEN = 15 * 60  # close the window after 15 minutes

# A small bar inside the window, so the person knows what this window is and what to do.
BANNER_JS = """
(() => {
  const add = () => {
    if (window.top !== window || document.getElementById('__wa_login_bar') || !document.body) return;
    const bar = document.createElement('div');
    bar.id = '__wa_login_bar';
    bar.textContent = %s;
    bar.style.cssText = 'position:fixed;left:50%%;bottom:14px;transform:translateX(-50%%);z-index:2147483647;' +
      'background:#F2B84B;color:#2A1D04;font:600 14px/1.4 Segoe UI,Arial,sans-serif;padding:9px 16px;' +
      'border-radius:999px;box-shadow:0 6px 20px rgba(0,0,0,.25);pointer-events:none;max-width:90vw;' +
      'text-align:center;direction:ltr';
    document.body.appendChild(bar);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', add); else add();
})();
"""

HAS_PASSWORD_JS = """() => [...document.querySelectorAll('input[type=password]')]
  .some(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; })"""


def write_status(site: str, state: str, message: str) -> None:
    STATUS.mkdir(parents=True, exist_ok=True)
    (STATUS / (safe_name(site) + ".json")).write_text(json.dumps(
        {"site": site, "state": state, "message": message,
         "at": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")


def site_cookies(state: dict, site: str) -> list[dict]:
    return [c for c in (state or {}).get("cookies", []) if _domain_match(c.get("domain", ""), site)]


def signed_in(before: set, last: dict | None, site: str, saw_password: bool, password_now: bool) -> bool:
    """True when the person really signed in: new cookies for the site appeared after the window opened, and
    either they went through a password form that is gone now, or a new private (httpOnly) cookie was set."""
    new = [c for c in site_cookies(last, site) if (c.get("name"), c.get("domain")) not in before]
    if not new or password_now:
        return False
    return saw_password or any(c.get("httpOnly") for c in new)


def main(url: str) -> int:
    from playwright.sync_api import sync_playwright

    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    site = domain_of(url)
    if not site:
        print("Give a website address, for example https://github.com/login")
        return 2
    SESSIONS.mkdir(parents=True, exist_ok=True)
    target = SESSIONS / (safe_name(site) + ".json")
    write_status(site, "open", f"The login window for {site} is open on the computer that runs the agent.")
    print(f"Log in to {site} in the window that opens, then close the window.")
    last, before, saw_password, password_now = None, set(), False, False
    with sync_playwright() as pw:
        kwargs = {"headless": False, "args": ["--window-size=1200,900"]}
        if settings.browser_channel and settings.browser_channel != "chromium":
            kwargs["channel"] = settings.browser_channel
        browser = pw.chromium.launch(**kwargs)
        context = browser.new_context(viewport={"width": 1200, "height": 800})
        text = (f"Agent login window for {site}: sign in here yourself, then close this window. "
                "The agent never sees your password.")
        context.add_init_script(BANNER_JS % json.dumps(text))
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
        except Exception:
            pass
        try:
            page.bring_to_front()
            before = {(c.get("name"), c.get("domain")) for c in site_cookies(context.storage_state(), site)}
        except Exception:
            pass
        started = time.time()
        while time.time() - started < MAX_OPEN:  # keep the latest state until the person closes the window
            try:
                pages = [p for p in context.pages if not p.is_closed()]
                if not pages:
                    break
                last = context.storage_state()
                try:
                    password_now = bool(pages[-1].evaluate(HAS_PASSWORD_JS))
                    saw_password = saw_password or password_now
                except Exception:
                    pass  # the page is navigating
                time.sleep(1)
            except Exception:
                break
        try:
            browser.close()
        except Exception:
            pass
    if not signed_in(before, last, site, saw_password, password_now):
        write_status(site, "not_signed_in", f"The window was closed before you signed in to {site}, so nothing "
                     "was saved. Press the button to try again, or reply \"skip\".")
        print(f"You did not sign in to {site}. Nothing was saved.")
        return 1
    keep = {"cookies": site_cookies(last, site),
            "origins": [o for o in last.get("origins", []) if site in o.get("origin", "")]}
    target.write_text(json.dumps(keep), encoding="utf-8")
    write_status(site, "saved", f"Your login for {site} is saved. Reply \"done\" so the agent uses it.")
    print(f"Saved your login for {site} ({len(keep['cookies'])} cookies). The agent can now use it.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
