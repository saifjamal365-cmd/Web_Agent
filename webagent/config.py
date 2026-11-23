"""All settings, read once from the environment (or a .env file next to the project).

Keys are never printed: use `describe()` for a safe summary.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None and sys.platform == "win32" and name in _WINDOWS_USER_VARS:
        value = _windows_user_env(name)
    return default if value in (None, "") else value


# Saved with [Environment]::SetEnvironmentVariable(..., "User") by the login scripts.
_WINDOWS_USER_VARS = {"GROQ_API_KEY"}


def _windows_user_env(name: str) -> str | None:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def _list(name: str) -> list[str]:
    return [d.strip().lower() for d in _env(name).split(",") if d.strip()]


@dataclass(frozen=True)
class Settings:
    groq_api_key: str = field(default_factory=lambda: _env("GROQ_API_KEY"))
    model: str = field(default_factory=lambda: _env("WA_MODEL", "openai/gpt-oss-120b"))
    vision_model: str = field(default_factory=lambda: _env("WA_VISION_MODEL", "qwen/qwen3.8-27b"))
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", f"sqlite:///{(DATA / 'webagent.db').as_posix()}"))
    redis_url: str = field(default_factory=lambda: _env("REDIS_URL", "redis://localhost:6379/0"))
    inline: bool = field(default_factory=lambda: _env("WA_INLINE", "0") == "1")
    browser_channel: str = field(default_factory=lambda: _env("WA_BROWSER_CHANNEL", "msedge" if sys.platform == "win32" else "chromium"))
    headless: bool = field(default_factory=lambda: _env("WA_HEADLESS", "1") != "0")
    max_steps: int = field(default_factory=lambda: int(_env("WA_MAX_STEPS", "40")))
    max_seconds: int = field(default_factory=lambda: int(_env("WA_MAX_SECONDS", "600")))
    approval_timeout: int = field(default_factory=lambda: int(_env("WA_APPROVAL_TIMEOUT", "900")))
    # A task that waits longer than this for the person gives its browser back (other tasks can run) and
    # continues from its checkpoint when the person answers. 0 = never (wait in place, as before).
    park_after: int = field(default_factory=lambda: int(_env("WA_PARK_AFTER", "30")))
    allowed_domains: list[str] = field(default_factory=lambda: _list("WA_ALLOWED_DOMAINS"))
    blocked_domains: list[str] = field(default_factory=lambda: _list("WA_BLOCKED_DOMAINS"))
    api_key: str = field(default_factory=lambda: _env("WA_API_KEY"))
    tracing: bool = field(default_factory=lambda: _env("WA_TRACING", "1") != "0")
    mlflow_uri: str = field(default_factory=lambda: _env("MLFLOW_TRACKING_URI", f"sqlite:///{(DATA / 'mlflow.db').as_posix()}"))
    live_dir: Path = field(default_factory=lambda: Path(_env("WA_LIVE_DIR", str(DATA / "live"))))
    port: int = field(default_factory=lambda: int(_env("WA_PORT", "8020")))
    # Windows desktop apps (UI Automation). On by default on Windows, only for the apps on this list.
    desktop: bool = field(default_factory=lambda: _env("WA_DESKTOP", "1" if sys.platform == "win32" else "0") == "1")
    desktop_apps: list[str] = field(default_factory=lambda: _list("WA_DESKTOP_APPS") or
                                    ["notepad", "calculator", "paint", "wordpad", "excel", "word"])
    helpers: int = field(default_factory=lambda: int(_env("WA_MAX_HELPERS", "4")))       # delegate: helper agents
    helper_steps: int = field(default_factory=lambda: int(_env("WA_HELPER_STEPS", "20")))
    # Optional: a Brave Search API key (free tier). Search sites block automated browsers more and more.
    brave_search_key: str = field(default_factory=lambda: _env("WA_BRAVE_SEARCH_KEY"))
    # The agent's Edge: a visible Edge window with its own profile where the person signs in themselves (works
    # for Google). Off = the agent uses its own hidden browser and saved logins (data/sessions).
    agent_edge: bool = field(default_factory=lambda: _env("WA_AGENT_EDGE", "0") == "1")
    agent_edge_port: int = field(default_factory=lambda: int(_env("WA_AGENT_EDGE_PORT", "9333")))
    agent_edge_profile: Path = field(default_factory=lambda: Path(_env(
        "WA_AGENT_EDGE_PROFILE", str(Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "WebAgent" / "edge-profile"))))

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")

    def describe(self) -> dict:
        """Safe summary for /api/health and logs (no secrets)."""
        return {
            "model": self.model,
            "vision_model": self.vision_model,
            "groq_key": f"set ({len(self.groq_api_key)} chars)" if self.groq_api_key else "not set",
            "db_engine": "postgresql" if self.is_postgres else "sqlite",
            "queue_mode": "inline thread" if self.inline else "redis + celery",
            "browser": "the agent's Edge window (your sign-ins)" if self.agent_edge else
                       f"{self.browser_channel} ({'headless' if self.headless else 'window'})",
            "agent_edge": self.agent_edge,
            "max_steps": self.max_steps,
            "api_key_required": bool(self.api_key),
            "tracing": "mlflow" if self.tracing else "off",
            "desktop_apps": self.desktop_apps if self.desktop and sys.platform == "win32" else "off",
            "search": "Brave Search API (key set), then search sites" if self.brave_search_key else
                      "search sites (DuckDuckGo, Bing, Brave, Wikipedia)",
        }


settings = Settings()
DATA.mkdir(parents=True, exist_ok=True)
