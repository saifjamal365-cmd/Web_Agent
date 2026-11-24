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
    # Arabic (sites in Arabic: the same buttons must stop for approval). Both hamza spellings, as people type both.
    "إرسال", "ارسال", "أرسل", "ارسل", "إرسال الرسالة", "ادفع", "دفع", "الدفع", "شراء", "اشتر", "اشتري", "اشتر الآن",
    "اطلب", "اطلب الآن", "إتمام الطلب", "اتمام الطلب", "إتمام الشراء", "اتمام الشراء", "تأكيد", "تاكيد", "أكد", "اكد",
    "تأكيد الطلب", "حذف", "احذف", "إزالة", "ازالة", "نشر", "انشر", "اشتراك", "اشترك", "إلغاء الاشتراك",
    "تسجيل حساب", "إنشاء حساب", "انشاء حساب", "احجز", "حجز", "تبرع", "تحويل", "سحب", "إيداع", "رفع", "تحميل ملف",
    "أوافق", "اوافق", "موافق", "قبول", "حفظ التغييرات", "تسجيل الخروج", "خروج",
    # Found missing on 24 Nov (64 common Arabic shop/app labels, 25 got through): submit/place order, complete the
    # order, clear, cancel an order or booking, register, save, install, activate, refund, tweet.
    "تقديم", "تقديم الطلب", "قدم طلبك", "قدم الطلب", "ضع طلبك", "ضع الطلب", "أكمل الطلب", "إكمال الطلب",
    "أكمل الشراء", "إكمال الشراء", "أكمل الدفع", "غرد", "مسح", "إلغاء الطلب", "إلغاء الحجز", "سجل الآن", "سجل حساب",
    "التسجيل", "حفظ", "تثبيت", "تفعيل", "استرداد", "طلب استرجاع", "طلب إرجاع",
    "cancel order", "cancel booking", "cancel reservation", "request refund",
]
# Very common, harmless words that contain or equal a risky word in some contexts.
SAFE_EXACT = {"search", "go", "next", "previous", "more", "show more", "load more", "filter", "sort", "menu",
              "close", "ok", "back", "home", "login", "log in", "sign in", "continue", "accept all cookies",
              "reject all", "decline", "reject", "necessary only", "only necessary",
              "بحث", "ابحث", "التالي", "التالية", "السابق", "المزيد", "عرض المزيد", "تحميل المزيد", "تصفية", "ترتيب",
              "القائمة", "إغلاق", "اغلاق", "رجوع", "الرئيسية", "تسجيل الدخول", "دخول", "متابعة", "رفض", "رفض الكل",
              "الضرورية فقط", "مسح البحث", "مسح الفلاتر", "مسح عوامل التصفية"}

# Arabic is written with or without short vowels and with several hamza / ta marbuta spellings, and words take
# attached prefixes (و ف ب ل ك, ال, لل). Eval 24 Nov: "إرسَال", "أرسِل", "تبرّع", "للإرسال", "عملية الشراء" all got
# through. Names and word lists are matched in one plain spelling, and Arabic words may carry a prefix.
_AR_MARKS = re.compile(r"[ً-ٰٟـ]")      # short vowels, shadda, sukun, dagger alef, tatweel
_ARABIC = re.compile(r"[؀-ۿ]")


def ar_plain(s: str) -> str:
    s = _AR_MARKS.sub("", s or "")
    return re.sub("[أإآٱ]", "ا", s).replace("ى", "ي").replace("ة", "ه")


_AR_PREFIX = r"(?:[وفبلك]|لل|ال|وال|بال|فال|كال|ولل)?"
_EN_RISKY = sorted({w for w in RISKY_WORDS if not _ARABIC.search(w)}, key=len, reverse=True)
_AR_RISKY = sorted({ar_plain(w) for w in RISKY_WORDS if _ARABIC.search(w)}, key=len, reverse=True)
_risky_re = re.compile(r"\b(" + "|".join(re.escape(w) for w in _EN_RISKY) + r")\b|(?<!\w)" + _AR_PREFIX + "("
                       + "|".join(re.escape(w) for w in _AR_RISKY) + r")(?!\w)", re.I)
_SAFE = {ar_plain(w) for w in SAFE_EXACT}
_AR_RISKY_LINK = re.compile(r"(?<!\w)" + _AR_PREFIX + "(" + "|".join(re.escape(ar_plain(w)) for w in (
    "حذف", "احذف", "إلغاء الاشتراك", "ادفع", "اشتر الآن", "إتمام الطلب", "إتمام الشراء", "تسجيل الخروج", "تحويل",
    "قدم طلبك", "إلغاء الطلب", "إلغاء الحجز")) + r")(?!\w)")


def _risky_word(m: re.Match) -> str:
    return m.group(1) or m.group(2)

SECRET_FIELD = re.compile(
    r"(passw|passcode|pwd|\bpin\b|otp|one.?time|2fa|mfa|totp|verification.?code|security.?code|cvv|cvc|csc|"
    r"card.?num|cc.?num|credit.?card|cardnumber|iban|swift|routing|account.?num|ssn|social.?security|"
    r"national.?id|passport|tax.?id|secret|api.?key|token|"
    r"كلمة (?:ال)?مرور|كلمة (?:ال)?سر|رمز (?:ال)?تحقق|رمز التأكيد|الرمز السري|رقم سري|رقم (?:ال)?بطاقة|"
    r"رمز (?:ال)?أمان|تاريخ الانتهاء|رقم (?:ال)?هوية|جواز (?:ال)?سفر|رقم (?:ال)?حساب|آيبان|ايبان)", re.I)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def risky_reason(element: dict, tool: str = "click") -> str | None:
    """Why this action needs approval, or None if it is safe to do without asking."""
    name = ar_plain(_norm(element.get("name", "")))
    role = element.get("role", "")
    if name in _SAFE:
        return None
    if role == "link" and not element.get("form"):
        # Plain links navigate; only flag clearly dangerous link text.
        m = re.search(r"\b(delete|remove|unsubscribe|pay|buy now|checkout|place order|log out|sign out|"
                      r"cancel subscription|close account|cancel order|transfer)\b", name, re.I) or \
            _AR_RISKY_LINK.search(name)
        return f'The link "{element.get("name")}" may change something for real ({m.group(1)}).' if m else None
    is_submit = role in ("button", "input:submit", "input:image") or element.get("type") == "submit"
    m = _risky_re.search(name)
    if m:
        return f'The {role or "element"} "{element.get("name")}" may change something for real ({_risky_word(m)}).'
    form = ar_plain(_norm(element.get("form", "")))
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
    if role in ("input:search", "searchbox") or re.search(r"search|query|\bq\b|find|filter|lookup|بحث|ابحث|تصفية",
                                                           name + " " + form + " " + _norm(element.get("field", ""))):
        return None
    if not form:
        # Chat and comment boxes have no <form>: Enter sends the message (live run: Google Chat's box was not
        # gated at all). In a document or a mail body Enter only starts a new line.
        hay = name + " " + _norm(element.get("field", ""))
        if NEW_LINE_BOX.search(hay):
            return None
        if role in ("textbox", "textarea") or re.search(r"\b(message|reply|comment|chat|post|tweet|write)|"
                                                        r"رسال|\bرد\b|الرد|تعليق|دردشة|اكتب", hay):
            return f'Pressing Enter sends what you typed in "{element.get("name") or "the message box"}".'
        return None
    return f'Pressing Enter submits the form "{form[:60]}".'


# Boxes where Enter starts a new line instead of sending (Gmail's "Message Body", a document editor).
NEW_LINE_BOX = re.compile(r"\b(body|document|editor|notes?|description|content|bio|draft|article|code|"
                          r"نص الرسالة|مستند|ملاحظات|ملاحظة|وصف|المحتوى|مسودة|مقال)\b")
# Shortcuts that send or submit wherever the focus is: Ctrl/Cmd+Enter (Gmail, Outlook, Slack, Teams, many forms),
# Alt+S (Outlook).
SEND_KEYS = re.compile(r"\{\s*(ctrl|control|cmd|command|meta|win)\s*\+\s*(enter|return)\s*\}|\{\s*alt\s*\+\s*s\s*\}",
                       re.I)
ENTER_KEY = re.compile(r"\n|\{\s*(enter|return)\s*\}", re.I)


def keys_reason(text: str, element: dict | None) -> str | None:
    """Why typing these keys needs approval: a send shortcut anywhere, or Enter in a box where Enter sends.
    Review 24 Nov: keys "{Ctrl+Enter}" in Gmail and "Hello{Enter}" in a chat box sent without approval, because only
    a raw line break into a given element was checked."""
    m = SEND_KEYS.search(text or "")
    if m:
        return f"The keys {m.group(0)} send or submit what was typed."
    if ENTER_KEY.search(text or "") and element:
        return submit_reason(element)
    return None


def keys_plain_text(text: str) -> str:
    """The words typed by a keys call, without {Key} presses, tabs or line breaks."""
    return re.sub(r"\s+", " ", re.sub(r"\{[^{}]{1,30}\}", " ", (text or "").replace("\t", " ").replace("\n", " "))).strip()


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
