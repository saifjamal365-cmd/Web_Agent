"""Safety rules in code. The model cannot talk its way around these.

- risky_reason(): a click/submit that may not be undoable -> needs a person's approval
- secret_field_reason(): a field for passwords, card numbers, codes -> the agent must never type
- domain_reason(): allow list / block list of websites
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from webagent.config import settings

# Words on a button/link/form that mean "this changes something for real".
RISKY_WORDS = [
    "pay", "payment", "buy", "purchase", "order", "place order", "checkout", "check out", "proceed to checkout",
    "send", "submit", "post", "publish", "tweet", "delete", "remove", "erase", "destroy", "confirm",
    "transfer", "withdraw", "deposit", "subscribe", "unsubscribe", "sign up", "register", "create account",
    "book", "book now", "reserve", "donate", "upload", "accept", "agree", "authorize", "authorise", "install",
    "save changes", "cancel subscription", "close account", "log out", "sign out",
]
# Very common, harmless words that contain or equal a risky word in some contexts.
SAFE_EXACT = {"search", "go", "next", "previous", "more", "show more", "load more", "filter", "sort", "menu",
              "close", "ok", "back", "home", "login", "log in", "sign in", "continue", "accept all cookies",
              "reject all", "decline", "reject", "necessary only", "only necessary"}

_risky_re = re.compile(r"\b(" + "|".join(re.escape(w) for w in sorted(RISKY_WORDS, key=len, reverse=True)) + r")\b",
                       re.I)

SECRET_FIELD = re.compile(
    r"(passw|passcode|pwd|\bpin\b|otp|one.?time|2fa|mfa|totp|verification.?code|security.?code|cvv|cvc|csc|"
    r"card.?num|cc.?num|credit.?card|cardnumber|iban|swift|routing|account.?num|ssn|social.?security|"
    r"national.?id|passport|tax.?id|secret|api.?key|token)", re.I)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def risky_reason(element: dict, tool: str = "click") -> str | None:
    """Why this action needs approval, or None if it is safe to do without asking."""
    name = _norm(element.get("name", ""))
    role = element.get("role", "")
    if name in SAFE_EXACT:
        return None
    if role == "link" and not element.get("form"):
        # Plain links navigate; only flag clearly dangerous link text.
        m = re.search(r"\b(delete|remove|unsubscribe|pay|buy now|checkout|place order|log out|sign out|"
                      r"cancel subscription|close account|transfer)\b", name, re.I)
        return f'The link "{element.get("name")}" may change something for real ({m.group(1)}).' if m else None
    is_submit = role in ("button", "input:submit", "input:image") or element.get("type") == "submit"
    m = _risky_re.search(name)
    if m:
        return f'The {role or "element"} "{element.get("name")}" may change something for real ({m.group(1)}).'
    form = _norm(element.get("form", ""))
    if is_submit and form:
        m = _risky_re.search(form) or re.search(r"(checkout|payment|order|cart|delete|contact|message|comment)", form)
        if m:
            return f'This button submits a form ({form[:60]}).'
    return None


def submit_reason(element: dict) -> str | None:
    """Pressing Enter in a field submits its form: risky unless it is a search/filter form."""
    form = _norm(element.get("form", ""))
    name = _norm(element.get("name", ""))
    role = element.get("role", "")
    if role in ("input:search", "searchbox") or re.search(r"search|query|\bq\b|find|filter|lookup", name + " " + form +
                                                           " " + _norm(element.get("field", ""))):
        return None
    if not form:
        # Chat and comment boxes have no <form>: Enter sends the message (live run: Google Chat's box was not
        # gated at all).
        hay = name + " " + _norm(element.get("field", ""))
        if role in ("textbox", "textarea") or re.search(r"\b(message|reply|comment|chat|post|tweet|write)", hay):
            return f'Pressing Enter sends what you typed in "{element.get("name") or "the message box"}".'
        return None
    return f'Pressing Enter submits the form "{form[:60]}".'


def secret_field_reason(element: dict) -> str | None:
    t = (element.get("type") or "").lower()
    ac = (element.get("autocomplete") or "").lower()
    if t == "password" or element.get("role") == "input:password":
        return "This is a password field."
    if ac.startswith("cc-") or ac in ("one-time-code", "current-password", "new-password"):
        return f"This field is for {ac}."
    hay = " ".join([element.get("name", ""), element.get("field", "")])
    m = SECRET_FIELD.search(hay)
    if m:
        return f'This field looks like it is for secret data ("{m.group(1)}").'
    return None


def looks_secret(text: str) -> bool:
    """The text itself looks like a card number or similar."""
    digits = re.sub(r"[\s-]", "", text or "")
    return bool(re.fullmatch(r"\d{13,19}", digits))


def domain_of(url: str) -> str:
    try:
        host = (urlparse(url if "://" in url else "https://" + url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _match(host: str, rules: list[str]) -> bool:
    return any(host == r or host.endswith("." + r) for r in rules)


def domain_reason(url: str) -> str | None:
    if not url or url.startswith(("about:", "data:")):
        return None
    if url.startswith(("file:", "chrome:", "edge:", "javascript:")):
        return "This kind of address is not allowed."
    host = domain_of(url)
    if settings.blocked_domains and _match(host, settings.blocked_domains):
        return f"{host} is on the blocked list."
    if settings.allowed_domains and not _match(host, settings.allowed_domains + ["duckduckgo.com"]):
        return f"{host} is not on the allowed list ({', '.join(settings.allowed_domains)})."
    return None
