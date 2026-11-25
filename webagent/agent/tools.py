"""The agent's tools: typed arguments (Pydantic), Groq tool schemas, and the code that runs them.

Every tool has a `why` argument, so each step in the trace says why the agent did it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal
import logging
import re
import time
from urllib.parse import quote_plus

from pydantic import BaseModel, Field, ValidationError

from webagent import desktop as D
from webagent import files as F
from webagent.agent import safety, vision
from webagent.browser.session import BrowserError, BrowserSession, Snapshot
from webagent.config import settings
from webagent.llm import LLM, LLMError

log = logging.getLogger(__name__)
# Asked automatically after a table was typed into a drawn app (a canvas spreadsheet has no page text to read).
DRAWN_CHECK = ("List the filled cells of the grid row by row with their addresses (for example A1: Date, B1: Product). "
               "Say clearly if several values are crammed into one cell, or if the table does not start at A1.")
SEARCH_URL = "https://html.duckduckgo.com/html/?q={q}"
# Search sites often block automated browsers (a bot check, or an empty results page). Each is tried in turn;
# Wikipedia's own search is last (it still answers automated browsers). With WA_BRAVE_SEARCH_KEY set, the
# Brave Search API (made for programs) is used first and the sites only when it fails.
SEARCH_ENGINES = [SEARCH_URL, "https://www.bing.com/search?q={q}", "https://search.brave.com/search?q={q}",
                  "https://en.wikipedia.org/w/index.php?search={q}&fulltext=1&ns0=1"]
BRAVE_API = "https://api.search.brave.com/res/v1/web/search"
BLOCK_MEMORY = 1800                     # seconds a site that blocked us is skipped (saves ~3 s per site)
_BLOCKED: dict[str, float] = {}         # search site -> skip it until this time
BLOCKED_TITLE = re.compile(r"captcha|access denied|forbidden|just a moment|attention required|are you a robot", re.I)
WHY = Field(description="One short sentence: why this is the right next step.")


class GoTo(BaseModel):
    """Open a web address in the current tab."""
    url: str = Field(description="Full address, for example https://www.python.org")
    why: str = WHY


class WebSearch(BaseModel):
    """Search the web and open the results page. Use when you do not know which website has the answer."""
    query: str
    why: str = WHY


class Click(BaseModel):
    """Click an element from the current page list (link, button, checkbox, tab...)."""
    ref: str = Field(description="Element ref from the latest page list, e.g. e12")
    why: str = WHY


class Type(BaseModel):
    """Type text into a field (replaces what is in it). Set submit=true to press Enter after typing."""
    ref: str
    text: str
    submit: bool = False
    why: str = WHY


class Select(BaseModel):
    """Choose an option in a dropdown (select) element."""
    ref: str
    option: str = Field(description="The option text as shown in the list")
    why: str = WHY


class Press(BaseModel):
    """Press a keyboard key on the page."""
    key: Literal["Enter", "Escape", "Tab", "PageDown", "PageUp", "ArrowDown", "ArrowUp", "Home", "End"]
    why: str = WHY


class Keys(BaseModel):
    """Type with the keyboard like a person, into what has focus (click ref first if given). Use it for apps that
    are not normal form fields: spreadsheets (Google Sheets, Excel), drawn/canvas apps, rich text editors, games.
    In text, \\t presses Tab and \\n presses Enter, so "Date\\tProduct\\nMon\\tMilk\\n" fills a 2x2 block of cells
    starting at the selected cell. {Ctrl+A}, {Backspace}, {Delete}, {ArrowDown}, {Escape} press that key."""
    text: str
    ref: str = Field(default="", description="Optional: element to click first (a cell area, an editor, a canvas)")
    why: str = WHY


class Hover(BaseModel):
    """Move the mouse over an element. Menus that open on hover (marked "opens a menu" or with ▾) then show their
    links in the next page list."""
    ref: str
    why: str = WHY


class Drag(BaseModel):
    """Drag an element and drop it on another one: kanban cards to a column, items in a sortable list."""
    ref: str = Field(description="The element to drag")
    to_ref: str = Field(description="Where to drop it (a column, a list, a drop zone)")
    why: str = WHY


class Scroll(BaseModel):
    """Scroll the page to see more elements."""
    direction: Literal["down", "up"] = "down"
    why: str = WHY


class Back(BaseModel):
    """Go back to the previous page."""
    why: str = WHY


class SwitchTab(BaseModel):
    """Switch to another browser tab (1 = first tab)."""
    index: int
    why: str = WHY


class ReadPage(BaseModel):
    """Read the full text of the current page (articles, prices, tables). Long pages come in parts."""
    part: int = Field(default=1, description="Part number, starting at 1")
    why: str = WHY


class FindInPage(BaseModel):
    """Find the paragraphs of the current page that contain some words (fast on long pages like Wikipedia)."""
    words: str = Field(description="A few key words, for example 'admitted United Nations' or 'elevation'")
    why: str = WHY


class Extract(BaseModel):
    """Pull facts out of the WHOLE current page in one step (no read_page parts): a list of products with name,
    price and specs, search results, reviews, details to compare. Answers only from the page text."""
    question: str = Field(description="What to pull out, e.g. 'every laptop: name, price, RAM, rating'")
    why: str = WHY


class Look(BaseModel):
    """Look with a vision model at a screenshot of the page, or at an image file the person gave you (file=).
    Use on the page only when the text list is not enough (images, charts, icons, canvas, the visual layout)."""
    question: str
    file: str = Field(default="", description="An image file from list_files (png, jpg, webp...) to look at "
                                              "instead of the page")
    why: str = WHY


class ReadTable(BaseModel):
    """Read the data tables of the current page as rows and columns (prices, specs, schedules, results,
    comparisons). Better than read_page for tables: every value stays in its column."""
    words: str = Field(default="", description="Optional words to pick the right table, e.g. 'price storage'")
    index: int = Field(default=0, description="Optional table number from an earlier read_table answer")
    why: str = WHY


class FormField(BaseModel):
    ref: str = Field(description="Field ref from the current page list")
    value: str = Field(description="Text to type, the option to choose, or true/false for a checkbox or radio")


class FillForm(BaseModel):
    """Fill several form fields in one step: text fields, dropdowns, checkboxes, radios, dates (YYYY-MM-DD).
    It does not send the form: click the form's button after it."""
    fields: list[FormField] = Field(description="The fields to fill, in page order")
    why: str = WHY


class UploadFile(BaseModel):
    """Attach one of the person's files (see list_files) to a file field or upload button of a form."""
    ref: str = Field(description="The file field or upload button ref")
    file: str = Field(description="File name from list_files")
    why: str = WHY


class ListFiles(BaseModel):
    """List the files you can use: files the person gave you (inbox) and files downloaded in this task.
    To download a file, click its link or button: it is saved automatically."""
    why: str = WHY


class ReadFile(BaseModel):
    """Read a file: PDF, Word (.docx), Excel (.xlsx), CSV, text, or an image (its text and what it shows, read by
    a vision model). Give a file name from list_files, or the address of a file on the web (for example a PDF
    link) to download and read it."""
    file: str = Field(default="", description="File name from list_files")
    url: str = Field(default="", description="Or: the web address of the file")
    part: int = Field(default=1, description="Part number for long files, starting at 1")
    why: str = WHY


class Login(BaseModel):
    """Use the person's own login for a website (for example their account on a shop or a portal). If they have
    not saved one yet, they are asked to log in themselves in a separate window. You never see or type the
    password."""
    site: str = Field(description="The website, e.g. github.com")
    why: str = WHY


class Delegate(BaseModel):
    """Split a big research task into independent parts, each done by a helper agent with its own browser
    (for example one helper per product to compare). Each part must make sense alone. Helpers only look things
    up: they never buy, send or change anything. You get each helper's answer and sources."""
    tasks: list[str] = Field(description="2 to 4 independent sub-tasks, each a full sentence")
    why: str = WHY


class DesktopOpen(BaseModel):
    """Open a Windows desktop app (only from the allowed list, e.g. notepad, calculator, excel, word, paint)."""
    app: str
    why: str = WHY


class DesktopRead(BaseModel):
    """Read the app window again as a list of elements with refs (d1, d2...). Optional: switch to another of
    the app windows you opened (1 = first)."""
    window: int = Field(default=0, description="Optional window number to switch to")
    why: str = WHY


class DesktopClick(BaseModel):
    """Click a button, menu item, tab or list item in the app window."""
    ref: str = Field(description="Element ref from the latest desktop list, e.g. d5")
    why: str = WHY


class DesktopType(BaseModel):
    """Type text into a text field or document of the app (replaces its text). enter=true presses Enter after."""
    ref: str
    text: str
    enter: bool = False
    why: str = WHY


class DesktopKeys(BaseModel):
    """Press keys in the app window. Syntax: ^ = Ctrl, % = Alt, + = Shift, {ENTER} {TAB} {F5} etc.
    Examples: '^a' (select all), '^c' (copy), '{F5}', '%f' (File menu). Typing keys is faster than clicking many
    buttons (Calculator: '1234*56='; write + as {+} and % as {%})."""
    keys: str
    why: str = WHY


class DesktopClose(BaseModel):
    """Close the active app window (the app may ask about unsaved changes: read and answer it)."""
    why: str = WHY


GroupName = Literal["tables", "forms", "files", "login", "helpers", "desktop"]


class MoreTools(BaseModel):
    """Get extra tools for this task (they stay available until the end). Groups: tables (read_table: data
    tables as rows and columns), forms (fill_form: fill many fields in one step), files (list_files; read_file for
    PDF, Word, Excel, CSV; upload_file), login (use the person's own login for a website), helpers (delegate the
    parts of a big research task to 2-4 helper agents), desktop (Windows apps: Notepad, Calculator, Excel, Word,
    Paint)."""
    groups: list[GroupName] = Field(description="One or more group names")
    why: str = WHY


class Note(BaseModel):
    """Save a fact you found, with the address where you saw it. Notes are kept for the final answer."""
    fact: str
    source: str = Field(description="The page address where you saw this fact")
    why: str = WHY


class AskUser(BaseModel):
    """Ask the person a question and wait for the reply. Use for a login, a CAPTCHA, a choice only they
    can make, or details that are missing from the task."""
    question: str
    why: str = WHY


class Finish(BaseModel):
    """End the task with the final answer for the person."""
    answer: str = Field(description="Complete answer in plain words, with the numbers/names found")
    sources: list[str] = Field(default_factory=list, description="Addresses of the pages the answer comes from")
    why: str = WHY


class GiveUp(BaseModel):
    """End the task because it cannot be done. Explain why and what was tried."""
    reason: str
    why: str = WHY


TOOLS: dict[str, type[BaseModel]] = {
    "go_to": GoTo, "web_search": WebSearch, "click": Click, "type": Type, "select": Select, "press": Press,
    "keys": Keys, "hover": Hover, "drag": Drag, "scroll": Scroll, "back": Back, "switch_tab": SwitchTab, "read_page": ReadPage,
    "find_in_page": FindInPage, "extract": Extract, "read_table": ReadTable, "look": Look,
    "fill_form": FillForm, "upload_file": UploadFile, "list_files": ListFiles, "read_file": ReadFile,
    "login": Login, "delegate": Delegate,
    "desktop_open": DesktopOpen, "desktop_read": DesktopRead, "desktop_click": DesktopClick,
    "desktop_type": DesktopType, "desktop_keys": DesktopKeys, "desktop_close": DesktopClose,
    "more_tools": MoreTools, "note": Note, "ask_user": AskUser, "finish": Finish, "give_up": GiveUp,
}
ENDING = {"finish", "give_up"}
DESKTOP = {"desktop_open", "desktop_read", "desktop_click", "desktop_type", "desktop_keys", "desktop_close"}
DESKTOP_IN_USE = DESKTOP - {"desktop_open"}   # shown only after an app was opened (keeps the tool list short)


# Every task gets the core tools. The other tools come in groups, so the model does not read 30 tool
# descriptions on every turn: a group is switched on by the task words, the plan, the current page, or the
# model itself (more_tools).
CORE = {"go_to", "web_search", "click", "type", "select", "press", "scroll", "back",
        "switch_tab", "read_page", "find_in_page", "extract", "look", "more_tools", "note", "ask_user", "finish",
        "give_up"}
GROUPS: dict[str, set[str]] = {
    "tables": {"read_table"},
    "gestures": {"keys", "hover", "drag"},
    "forms": {"fill_form"},
    "files": {"list_files", "read_file", "upload_file"},
    "login": {"login"},
    "helpers": {"delegate"},
    "desktop": set(DESKTOP),
}
HELPER_NO_GROUPS = {"login", "helpers", "desktop"}
FREE_GROUPS = {"gestures", "tables", "forms"}   # harmless: a call to one of their tools just works
GROUP_HELP = {
    "tables": "tables (read_table: data tables as rows and columns)",
    "gestures": "gestures (keys: real keyboard for spreadsheets and editors; hover: menus that open on hover; drag)",
    "forms": "forms (fill_form: fill many fields in one step)",
    "files": "files (list_files; read_file for PDF, Word, Excel, CSV; upload_file)",
    "login": "login (use the person's own login for a website)",
    "helpers": "helpers (delegate the parts of a big research task to 2-4 helper agents)",
    "desktop": "desktop (Windows apps: Notepad, Calculator, Excel, Word, Paint)",
}


def missing_groups(groups, helper: bool = False) -> list[str]:
    """The groups more_tools can still add (none: more_tools is not offered)."""
    out = [g for g in GROUPS if g not in set(groups or ())]
    if helper:
        out = [g for g in out if g not in HELPER_NO_GROUPS]
    if not D.available():
        out = [g for g in out if g != "desktop"]
    return out

TASK_WORDS = {
    "tables": r"\btables?\b|compar|\bspecs?\b|specification|schedule|timetable|price list|ranking|standings|"
              r"\bvs\.?\b|versus|cheapest|most expensive|longest|biggest|highest rated",
    "forms": r"\bforms?\b|\bfill\b|apply|application|register|sign ?up|booking|\bbook (a|an|the)\b|survey|"
             r"contact (form|us)|subscribe|questionnaire",
    "files": r"\bfiles?\b|\bpdf\b|\bcsv\b|excel|xlsx|\.docx?\b|word document|download|upload|attach|\bcv\b|"
             r"r[eé]sum[eé]|spreadsheet|invoice|brochure|manual|my documents?|"
             # images the person gives (live 25 Nov: "check this image" got no list_files and asked for an upload)
             r"\bimages?\b|\bpictures?\b|\bphotos?\b|screenshot|\.(png|jpe?g|webp|gif|bmp)\b|صور|ملف|مرفق",
    "login": r"log ?in|sign ?in|my account|my orders?|my profile|my inbox|my cart|my bookings?|portal",
    "helpers": r"compar|\beach of\b|several|multiple|\bvs\.?\b|versus|research",
    "gestures": r"spreadsheet|\bsheets?\b|excel|\bcells?\b|\bdrag|\bhover|keyboard|shortcut|\bkeys\b|editor|"
                r"canvas|\bdraw|kanban|\bboard\b|trello|\bmenu",
    "desktop": r"notepad|calculator|\bcalc\b|\bexcel\b|microsoft word|ms word|word document|wordpad|\bpaint\b|"
               r"desktop app|windows app|on my (pc|computer|laptop)",
}
FILE_LINK = re.compile(r"\.(pdf|csv|xlsx?|docx?)(\?|#|$)", re.I)
FORM_ROLES = re.compile(r"^(input:(text|email|tel|number|date|datetime-local|time|url|checkbox|radio|month|week)"
                        r"|select|textarea|combobox|textbox|checkbox|radio|switch|listbox)$")  # + custom (ARIA) ones


def task_groups(task: str) -> set[str]:
    """Tool groups the words of the task point to (the planner can add more)."""
    text = (task or "").lower()
    return {g for g, pattern in TASK_WORDS.items() if re.search(pattern, text)}


def page_groups(snap: Snapshot | None) -> set[str]:
    """Tool groups the current page needs: a data table, a form, a file field or file links, a login form."""
    if snap is None:
        return set()
    out = set()
    if getattr(snap, "tables", 0):
        out.add("tables")
    roles = [str(el.get("role") or "") for el in snap.refs.values()]
    if sum(1 for r in roles if FORM_ROLES.match(r)) >= 3:
        out.add("forms")
    if "input:file" in roles or any(FILE_LINK.search(str(el.get("href") or "")) for el in snap.refs.values()):
        out.add("files")
    if "input:password" in roles:
        out.add("login")
    # drawn apps, rich editors, drag boards and hover menus need the keyboard / mouse gestures
    if any(r in ("canvas", "draggable", "drop area", "textbox") for r in roles) or any(
            el.get("popup") for el in snap.refs.values()):
        out.add("gestures")
    return out


def tools_for(groups) -> set[str]:
    names = set(CORE)
    for g in groups or ():
        names |= GROUPS.get(g, set())
    return names


def available(desktop_open: bool = False, helper: bool = False, groups=None) -> set[str]:
    """The tools the model gets this turn. groups=None: every tool (all groups at once)."""
    if groups is None:
        names = set(TOOLS) - {"more_tools"}
    else:
        names = tools_for(groups)
        if desktop_open:
            names |= DESKTOP  # an app window is open: keep its tools
    if not D.available():
        names -= DESKTOP
    elif not desktop_open:
        names -= DESKTOP_IN_USE
    if helper:  # helpers look things up only: no helpers of their own, no desktop, no logins or uploads
        names -= DESKTOP | {"delegate", "login", "upload_file", "ask_user"}
    return names
SHOWS_PAGE = {"go_to", "web_search", "click", "type", "select", "press", "scroll", "back", "switch_tab"}


def _clean_schema(schema: dict) -> dict:
    schema = dict(schema)
    schema.pop("title", None)
    schema.pop("description", None)
    props = {}
    for k, v in schema.get("properties", {}).items():
        v = {kk: vv for kk, vv in v.items() if kk != "title"}
        props[k] = v
    schema["properties"] = props
    return schema


def _inline_defs(schema: dict) -> dict:
    """Groq wants plain schemas: put nested models ($defs, e.g. form fields) in place."""
    defs = schema.pop("$defs", {})

    def walk(x):
        if isinstance(x, dict):
            if "$ref" in x:
                return walk(_clean_schema(dict(defs[x["$ref"].split("/")[-1]])))
            return {k: walk(v) for k, v in x.items()}
        if isinstance(x, list):
            return [walk(v) for v in x]
        return x
    return walk(schema)


def schemas(only: set[str] | None = None, missing: list[str] | None = None) -> list[dict]:
    """Groq tool schemas. `missing`: the groups more_tools can still add (it then names only those)."""
    out = []
    for name, model in TOOLS.items():
        if only is not None and name not in only:
            continue
        description = " ".join((model.__doc__ or "").split())
        parameters = _inline_defs(_clean_schema(model.model_json_schema()))
        if name == "more_tools" and missing is not None:
            if not missing:
                continue
            description = ("Get extra tools you do not have yet (they stay until the end of the task). Groups: " +
                           "; ".join(GROUP_HELP[g] for g in missing) + ".")
            parameters["properties"]["groups"]["items"]["enum"] = list(missing)
        out.append({"type": "function", "function": {"name": name, "description": description,
                                                      "parameters": parameters}})
    return out


def validate(name: str, args: dict) -> BaseModel:
    if name not in TOOLS:
        raise ValueError(f"There is no tool named {name}. Use one of: {', '.join(TOOLS)}.")
    args = dict(args or {})
    args.setdefault("why", "")
    try:
        return TOOLS[name].model_validate(args)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:3])
        fields = [f for f in TOOLS[name].model_fields if f != "why"]
        given = set(args) - {"why"}
        # Live run 25 Nov: note was called 3 times with {"answer": ...}; the model meant finish
        fits = [t for t, m in TOOLS.items() if t != name and given and given <= set(m.model_fields)
                and all(f in given for f, info in m.model_fields.items() if info.is_required() and f != "why")]
        hint = f" {name} takes: {', '.join(fields)}." + (f" These arguments fit {fits[0]}: call {fits[0]} instead."
                                                       if fits else "")
        raise ValueError(f"Wrong arguments for {name}: {problems}.{hint}") from exc


# ---------------------------------------------------------------- safety gate

@dataclass
class Gate:
    kind: Literal["ok", "approval", "refuse", "question"]
    reason: str = ""


def gate(name: str, args: BaseModel, snap: Snapshot | None, last_typed: dict | None = None,
         desktop: "D.DesktopSession | None" = None) -> Gate:
    """Decide, in code, if an action can run, needs a person, or is refused."""
    if name == "ask_user":
        if ASKS_FOR_LOGIN.search(args.question):
            return Gate("refuse", "Never ask the person for login details (email, username, password, codes). "
                                  "Call login with the website: the person logs in themselves.")
        return Gate("question", args.question)
    if name == "login":
        return Gate("question", login_question(args.site))
    if name == "fill_form":
        return _gate_form(args, snap)
    if name == "upload_file":
        if snap is not None:
            try:
                snap.element(args.ref)
            except BrowserError as exc:
                return Gate("refuse", str(exc))
        return Gate("approval", f'Upload your file "{args.file}" to {snap.url if snap else "this website"}.')
    if name == "read_file" and args.url:
        r = safety.domain_reason(args.url)
        return Gate("refuse", r) if r else Gate("ok")
    if name in DESKTOP:
        return _gate_desktop(name, args, desktop)
    if name == "go_to":
        r = safety.domain_reason(args.url)
        return Gate("refuse", r) if r else Gate("ok")
    if name in ("click", "type", "select") and re.fullmatch(r"\s*d\d+\s*", args.ref):
        tool = {"click": "desktop_click", "type": "desktop_type", "select": "desktop_click"}[name]
        return Gate("refuse", f"{args.ref} is an element of the desktop app, not of the web page: use {tool}.")
    if name in ("click", "type", "select") and snap is not None:
        try:
            el = snap.element(args.ref)
        except BrowserError as exc:
            return Gate("refuse", str(exc))
        if name == "type":
            r = safety.secret_field_reason(el)
            if r or safety.looks_secret(args.text):
                return Gate("refuse", (r or "This text looks like a card or account number.") +
                            " The agent never types passwords, card numbers or codes. Use ask_user so the person "
                            "can do it.")
            if str(el.get("role") or "").startswith("input") and ("\t" in args.text or "\n" in args.text):
                return Gate("refuse", f'{args.ref} "{el.get("name", "")}" is a one-line field: a table typed there is '
                            "lost (a spreadsheet's Name box shows an error). To fill a grid, use keys on the grid: "
                            "\"{Ctrl+Home}\" then the rows, \\t between cells and \\n after each row, in ONE keys call.")
            if args.submit:
                r = safety.submit_reason(el)
                return Gate("approval", r) if r else Gate("ok")
            return Gate("ok")
        if name == "click":
            if el.get("href") and el.get("role") == "link":
                href = el["href"]
                if href.startswith("http"):
                    r = safety.domain_reason(href)
                    if r:
                        return Gate("refuse", r)
            r = safety.risky_reason(el)
            return Gate("approval", r) if r else Gate("ok")
    if name == "press" and args.key == "Enter" and last_typed:
        r = safety.submit_reason(last_typed)
        return Gate("approval", r) if r else Gate("ok")
    if name in ("hover", "drag"):
        if snap is not None:
            try:
                snap.element(args.ref)
                if name == "drag":
                    snap.element(args.to_ref)
            except BrowserError as exc:
                return Gate("refuse", str(exc))
        return Gate("ok")
    if name == "keys":
        el = {}
        if args.ref and snap is not None:
            try:
                el = snap.element(args.ref)
            except BrowserError as exc:
                return Gate("refuse", str(exc))
        r = safety.secret_field_reason(el) if el else None
        if r or safety.looks_secret(args.text):
            return Gate("refuse", (r or "This text looks like a card or account number.") +
                        " The agent never types passwords, card numbers or codes. Use ask_user so the person can do it.")
        # A send shortcut, or Enter in a box where Enter sends: without a ref the keys go to what has focus,
        # which is the box typed into last.
        r = safety.keys_reason(args.text, el or last_typed)
        return Gate("approval", r) if r else Gate("ok")
    return Gate("ok")


# Personal details (name, email, phone, address) must come from the person, never be made up by the model.
PERSONAL_FIELD = re.compile(r"\b(e-?mail|full[ _-]?name|first[ _-]?name|last[ _-]?name|surname|your name|phone|mobile|"
                            r"telephone|street|city|zip|postal|post ?code|date of birth|birthday|national id|"
                            r"(home|street|postal|your) address)\b", re.I)
PERSONAL_AUTOCOMPLETE = re.compile(r"^(name|given-name|family-name|email|tel|street-address|address-line\d|"
                                   r"postal-code|bday|address-level\d)", re.I)
# The person allows made-up values ("fill it with random answers", "use test data")
ALLOWS_MADE_UP = re.compile(r"\b(random|dummy|fake|made[- ]up|invent\w*|placeholder|any name|"
                            r"(example|sample|test) (data|details|values|answers|name|user|info\w*))\b", re.I)


def _personal(el: dict) -> bool:
    if el.get("type") in ("email", "tel") or PERSONAL_AUTOCOMPLETE.match(el.get("autocomplete") or ""):
        return True
    if not str(el.get("role", "")).startswith(("input:", "textarea", "textbox")):
        return False
    label = re.sub(r"[\s*:]+$", "", str(el.get("name", "")).strip().lower())
    if label in ("name", "your name", "full name", "naam", "nom") or "name" in str(el.get("field", "")).split():
        return True        # a field called just "Name" (not "Product name", "Name box")
    return bool(PERSONAL_FIELD.search(f"{el.get('name', '')} {el.get('field', '')}"))


def _known(value: str, known: str) -> bool:
    """Is every word / number of `value` somewhere in what the person wrote?"""
    words = re.findall(r"[\w@.+-]+", value.lower())
    return all(w in known for w in words if len(w) >= 2)


def invented_personal(name: str, args: BaseModel, snap: Snapshot | None, known: str) -> str:
    """A refusal when the agent types personal details (a name, email, phone, address) that the person never gave.
    `known` is the task, the person's replies and earlier chat in lower case. Empty string when it is fine."""
    if snap is None or ALLOWS_MADE_UP.search(known):
        return ""
    pairs = []
    if name == "fill_form":
        pairs = [(f.ref, f.value) for f in args.fields]
    elif name == "type":
        pairs = [(args.ref, args.text)]
    bad = []
    for ref, value in pairs:
        try:
            el = snap.element(ref)
        except BrowserError:
            continue
        if value.strip() and _personal(el) and not _known(value, known):
            bad.append(f'{ref} ({el.get("name") or "field"}) = "{value[:40]}"')
    if not bad:
        return ""
    return ("These are personal details the person did not give you: " + "; ".join(bad) + ". Never invent names, "
            "emails, phone numbers or addresses. Ask the person with ask_user for ALL missing details in one question.")


def describe_action(name: str, args: BaseModel, snap: Snapshot | None) -> str:
    """Plain words for the approval card: what will happen if the person says yes."""
    d = args.model_dump(exclude={"why"})
    if name in ("click", "type", "select") and snap is not None and d.get("ref") in snap.refs:
        el = snap.refs[d["ref"]]
        target = f'{el.get("role")} "{el.get("name")}"'
        if name == "click":
            return f"Click {target} on {snap.url}"
        if name == "type":
            return f'Type "{d["text"]}" into {target}' + (" and press Enter" if d.get("submit") else "")
        return f'Select "{d["option"]}" in {target}'
    if name == "fill_form":
        return "Fill in: " + "; ".join(f"{f['ref']} = {f['value']}" for f in d["fields"]) + (f" on {snap.url}" if snap else "")
    if name == "upload_file":
        return f'Upload the file "{d["file"]}" on {snap.url if snap else "the page"}'
    if name == "desktop_click":
        return f"Click {d['ref']} in the desktop app"
    if name == "desktop_keys":
        return f"Press {d['keys']} in the desktop app"
    if name == "press":
        return f"Press {d['key']} on {snap.url if snap else 'the page'}"
    if name == "keys":
        return f'Type with the keyboard: "{d["text"][:200]}" on {snap.url if snap else "the page"}'
    return f"{name} {d}"


ASKS_FOR_LOGIN = re.compile(
    r"(password|passcode|credential|one.?time code|verification code|2fa|otp\b|"
    r"(e-?mail|username|user name|phone)[^.?!]{0,60}(account|sign.?in|log.?in|login)|"
    r"(account|sign.?in|log.?in|login)[^.?!]{0,60}(e-?mail|username|user name|phone)|"
    r"كلمة (?:ال)?مرور|كلمة (?:ال)?سر|رمز (?:ال)?تحقق|رمز التأكيد|الرمز السري|رقم سري|"
    r"(البريد|الإيميل|الايميل|اسم المستخدم|رقم (?:ال)?هاتف|الجوال)[^.?!؟]{0,60}(حساب|تسجيل الدخول|الدخول)|"
    r"(حساب|تسجيل الدخول|الدخول)[^.?!؟]{0,60}(البريد|الإيميل|الايميل|اسم المستخدم|رقم (?:ال)?هاتف|الجوال))", re.I)


def login_question(site: str) -> str:
    if settings.agent_edge:
        return (f"The task needs you to sign in to {site}. Press the button to show its sign-in page in the agent's "
                f"Edge window, sign in there yourself (the agent never sees your password), then reply \"done\". "
                f"Reply \"skip\" if you do not want to.")
    return (f"The task needs your login for {site}. Press \"Open login window\", log in there yourself (the agent "
            f"never sees your password), close that window, then reply \"done\". If the site refuses to sign you in "
            f"in that window (Google often does), or you do not want to, reply \"skip\".")


def _gate_form(args: FillForm, snap: Snapshot | None) -> Gate:
    if not args.fields:
        return Gate("refuse", "No fields given.")
    risky = []
    for f in args.fields:
        if snap is None:
            break
        try:
            el = snap.element(f.ref)
        except BrowserError as exc:
            return Gate("refuse", str(exc))
        r = safety.secret_field_reason(el)
        if r or safety.looks_secret(f.value):
            return Gate("refuse", f"{f.ref}: " + (r or "this looks like a card or account number.") +
                        " The agent never types passwords, card numbers or codes. Leave it for the person.")
        if el.get("role") in ("input:checkbox", "input:radio", "checkbox", "radio", "switch"):
            on = f.value.strip().lower() not in ("false", "no", "0", "off", "uncheck", "")
            m = on and safety.risky_reason({**el, "role": "button"})
            if m:  # "I agree to the terms", "Subscribe to the newsletter"
                risky.append(f'tick "{el.get("name")}"')
    if risky:
        return Gate("approval", "Filling this form would " + ", ".join(risky) + ".")
    return Gate("ok")


def _gate_desktop(name: str, args: BaseModel, desktop) -> Gate:
    if name == "desktop_open":
        return Gate("ok") if D.app_key(args.app) else Gate(
            "refuse", f"{args.app!r} is not on the allowed apps list: {', '.join(D.allowed_apps())}.")
    if desktop is None:
        return Gate("refuse", "No app is open. Use desktop_open first.")
    if name in ("desktop_click", "desktop_type"):
        try:
            el = desktop.element(args.ref)
        except D.DesktopError as exc:
            return Gate("refuse", str(exc))
        if name == "desktop_type":
            if el.get("password") or safety.SECRET_FIELD.search(el.get("name", "")) or safety.looks_secret(args.text):
                return Gate("refuse", "The agent never types passwords, card numbers or codes. Leave it for the person.")
            return Gate("ok")
        r = safety.risky_reason({"name": el.get("name", ""), "role": "button"})
        if not r and re.fullmatch(r"(save|save as|don'?t save|replace|overwrite|yes|ok|print)", el.get("name", "").strip().lower()):
            r = f'The button "{el.get("name")}" may change a file or the computer.'
        return Gate("approval", r) if r else Gate("ok")
    if name == "desktop_keys" and D.RISKY_KEYS.search(args.keys):
        return Gate("approval", f"The keys {args.keys} may save, print or close something.")
    return Gate("ok")


# ---------------------------------------------------------------- run

@dataclass
class ToolResult:
    text: str
    ok: bool = True
    tokens: int = 0
    note: dict | None = None
    end: dict | None = field(default=None)  # {"status": "done"|"failed", ...}
    groups: list[str] | None = None         # more_tools: tool groups to switch on


@dataclass
class Env:
    """What a tool may use besides the browser."""
    task_id: str = "local"
    desktop: "D.DesktopSession | None" = None
    task: str = ""  # the person's task: extract adds the details its requirements need

    def open_desktop(self) -> "D.DesktopSession":
        if self.desktop is None:
            self.desktop = D.DesktopSession()
        return self.desktop


def _api_search(query: str, key: str) -> str:
    """Brave Search API: results as text (the model opens one with go_to)."""
    import httpx
    r = httpx.get(BRAVE_API, params={"q": query, "count": 8}, timeout=15,
                  headers={"Accept": "application/json", "X-Subscription-Token": key})
    r.raise_for_status()
    results = ((r.json() or {}).get("web") or {}).get("results") or []
    if not results:
        return ""
    lines = [f'Search results for "{query}" (Brave Search API). Open one with go_to:']
    for i, it in enumerate(results[:8], 1):
        snippet = re.sub(r"<[^>]+>", "", str(it.get("description") or ""))[:220]
        lines.append(f"{i}. {str(it.get('title') or '')[:120]}\n   {it.get('url')}\n   {snippet}")
    return "\n".join(lines)


def _blocked(snap: Snapshot) -> str:
    """Why a search page is no use: a bot check, an error page, or no results (what sites show to bots)."""
    if snap.captcha or BLOCKED_TITLE.search(snap.title or ""):
        return "showed a bot check"
    results = [el for el in snap.refs.values() if el.get("role") == "link" and len(el.get("name") or "") >= 20]
    return "showed no results" if len(results) < 3 else ""


def _search(browser: BrowserSession, query: str, search_url: str) -> str:
    if search_url != SEARCH_URL:  # tests and custom setups use exactly their own search page
        return browser.goto(search_url.format(q=quote_plus(query)))
    tried = []
    key = settings.brave_search_key
    if key:
        try:
            text = _api_search(query, key)
            if text:
                return text
            tried.append("Brave Search API found nothing")
        except Exception as exc:  # no key details in the message
            tried.append(f"Brave Search API failed ({type(exc).__name__})")
    now = time.time()
    for url in SEARCH_ENGINES:
        site = safety.domain_of(url)
        if url != SEARCH_ENGINES[-1] and _BLOCKED.get(site, 0) > now:
            tried.append(f"{site} blocked earlier")
            continue
        try:
            text = browser.goto(url.format(q=quote_plus(query)))
        except BrowserError as exc:
            tried.append(f"{safety.domain_of(url)} failed ({str(exc)[:80]})")
            continue
        try:
            why = _blocked(browser.snapshot())
        except BrowserError:
            why = ""
        if not why:
            _BLOCKED.pop(site, None)
            return text + (f" (Other search engines: {'; '.join(tried)}.)" if tried else "")
        tried.append(f"{site} {why}")
        if BLOCK_MEMORY:
            _BLOCKED[site] = now + BLOCK_MEMORY
    raise BrowserError("Web search is blocked for this browser right now (" + "; ".join(tried) + "). Do not call "
                       "web_search again: use go_to with a website you know (the official website, or "
                       "https://en.wikipedia.org/wiki/<Topic>).")


EXTRACT_PROMPT = (
    "You pull facts out of one web page for a browsing agent. Use ONLY the page text below; never add facts from "
    "memory. Copy names, prices and numbers exactly as the page writes them. For lists (products, results), give "
    "one line per item: name | price | the asked details | the words of its link or button (so the agent can find "
    "it). Mark a detail the page does not give as 'not shown'. For one fact (a headline, a title, a date, a number) "
    "write one plain line with just that fact: no columns, no 'not shown'. "
    "Give a value only for the exact thing asked: if the page gives it for a related thing (the country, not its "
    "capital city; the whole brand, not one product), write whose value it is, or 'not shown' for the asked thing. The page is often already filtered (a label, a "
    "folder, a search, a category): words in the request that describe the whole list (for example 'important "
    "emails' on the Important view) are not a test for each item, so list the items the page shows. Say "
    "'Nothing found' only when the page really has no such items. Be short: each item ONCE (a page often shows the "
    "same product twice, as a photo link and a title link), no repeated lines.")


def _dedupe_lines(text: str) -> str:
    """Drop repeated identical lines. With low reasoning the model sometimes loops on one line ("ThinkPad T470 |
    SAR 1,245.00 | ..." 20 times on amazon.sa, 24 Nov)."""
    out, seen, dropped = [], set(), 0
    for line in text.splitlines():
        key = re.sub(r"\s+", " ", line).strip(" -*• ").lower()
        if key and len(key) > 12 and key in seen:
            dropped += 1
            continue
        seen.add(key)
        out.append(line)
    return "\n".join(out) + (f"\n({dropped} repeated lines removed)" if dropped >= 3 else "")


def _extract(browser: BrowserSession, llm: LLM, question: str, hint: str = "", task: str = "") -> ToolResult:
    """Answer a question from the whole page text with one model call, so the page text never enters the agent's
    own context (it only gets the short answer)."""
    text = browser.full_text(40000)
    if not text.strip():
        return ToolResult(f"The page ({browser.url}) has no readable text. Try look." + hint)
    messages = [{"role": "system", "content": EXTRACT_PROMPT},
                {"role": "user", "content": f"Page: {browser.url}\n<page>\n{text}\n</page>\n\nWhat to pull out: "
                                            f"{question}" + (
                    f"\n\nThe agent works on this task: {task}\nAlso give, for every item, each detail the task's "
                    "requirements are about (for example RAM, size, rating, date), even if the question above forgot it."
                    if task else "")}]
    try:
        # Low reasoning: copying facts from a page needs little thought, and with medium reasoning the hidden
        # reasoning used up the whole budget on a 48-item amazon.sa page, the reply came back EMPTY and was reported
        # as "Nothing found", so the agent extracted the same page again and again (24 Nov: 41 steps, 624k tokens).
        reply = llm.chat(messages, max_tokens=3000, temperature=0.0, reasoning="low")
    except LLMError as exc:
        return ToolResult(f"Could not extract: {exc}. Use find_in_page or read_page.")
    if not (reply.text or "").strip():
        if getattr(reply, "cut", False):
            return ToolResult("Could not extract: the reply was cut off before it gave anything. Ask for fewer details "
                              "(only what the task needs), or use find_in_page.", ok=False, tokens=reply.tokens)
        return ToolResult("Could not extract: the model gave no reply. Try once more or use find_in_page.", ok=False,
                          tokens=reply.tokens)
    answer = _dedupe_lines(reply.text.strip())
    if getattr(reply, "cut", False):
        answer += "\n(The list was cut off here: ask for fewer details or only the items that meet the task.)"
    if re.match(r"(?i)\W*(nothing|no (items|results|matches)) (found|match)", answer) and len(answer) < 120:
        hint += ("\nIf the page does show a list, ask again in plain words for the rows as they appear (for example "
                 "\"every row in the list: sender, subject, date\"), without describing words the rows may not show.")
    cut = " (the page was long: only its first 40000 characters were used)" if len(text) >= 40000 else ""
    last = browser.last.refs.values() if browser.last and browser.last.url == browser.url else []
    is_list = sum(1 for l in answer.splitlines() if l.strip()) >= 3   # one headline is not a list to page through
    if is_list and any(re.fullmatch(r"(next|next page|more results|show more|load more|[›»>]|التالي|الصفحة التالية|التالية|"
                        r"المزيد|عرض المزيد|تحميل المزيد|المزيد من النتائج)", str(el.get("name") or "").strip(), re.I)
           for el in last):
        hint += ("\nThis list goes on: the page has a Next / Show more control. If the task needs the whole list (the "
                 "cheapest, the best, the only one, all, a comparison), open it and extract again BEFORE you answer; "
                 "the answer is checked against every page.")
    return ToolResult(f"From {browser.url}{cut}:\n{answer[:3000]}" + hint, tokens=reply.tokens)


def run(name: str, args: BaseModel, browser: BrowserSession, llm: LLM, snap: Snapshot | None,
        search_url: str = SEARCH_URL, env: Env | None = None) -> ToolResult:
    env = env or Env()
    try:
        if name in DESKTOP:
            return _run_desktop(name, args, browser, env)
        if name != "look":
            browser.live_source = None  # back in the browser: the live view shows the page again
        if name == "go_to":
            return ToolResult(browser.goto(args.url))
        if name == "web_search":
            return ToolResult(_search(browser, args.query, search_url))
        if name == "read_table":
            return ToolResult(browser.tables(args.words, args.index))
        if name == "fill_form":
            return ToolResult(browser.fill_form([f.model_dump() for f in args.fields]))
        if name == "upload_file":
            return ToolResult(browser.upload(args.ref, F.resolve(args.file, env.task_id)))
        if name == "list_files":
            items = F.list_files(env.task_id)
            if not items:
                return ToolResult("No files yet. The person can add files in the dashboard; downloads appear here.")
            out = "Files: " + "; ".join(f"{i['name']} ({i['where']}, {F.human_size(i['size'])}"
                                        f"{', image' if F.is_image(i['name']) else ''})" for i in items)
            if any(F.is_image(i["name"]) for i in items):
                # Live 25 Nov: the agent called look (a screenshot of the empty browser tab) for an uploaded PNG and
                # reported the image as blank, twice.
                out += ("\nTo see an image file, call look with file=\"<its name>\" (or read_file for all its text). "
                        "Plain look only sees the browser page, not your files.")
            return ToolResult(out)
        if name == "read_file":
            if args.url:
                path = browser.fetch_file(args.url)
            elif args.file:
                path = F.resolve(args.file, env.task_id)
            else:
                return ToolResult("Give a file name (see list_files) or a url.", ok=False)
            if F.is_image(path):
                text, tokens = vision.look_file(llm, path, vision.READ_ALL)
                return ToolResult(f"Image {path.name}, read by the vision model:\n{text}", tokens=tokens)
            return ToolResult(F.read_part(path, args.part))
        if name == "login":
            dom = safety.domain_of(args.site)
            if browser.load_login(dom):
                return ToolResult(f"Your saved login for {dom} is now used by the browser. Open or reload the page.")
            return ToolResult(f"There is no saved login for {dom}. Continue without it (public pages only), or "
                              "finish and say that the task needs the person's login.", ok=False)
        if name == "click":
            return ToolResult(browser.click(args.ref))
        if name == "type":
            return ToolResult(browser.type(args.ref, args.text, args.submit))
        if name == "select":
            return ToolResult(browser.select(args.ref, args.option))
        if name == "press":
            return ToolResult(browser.press(args.key))
        if name == "keys":
            out = browser.keys(args.text, args.ref or None)
            if "The app is a drawing" in out and ("\t" in args.text or "\n" in args.text):
                # A drawn grid has no page text: show the agent (and the checker) what really landed where.
                try:
                    seen, tokens = vision.look(llm, browser.screenshot(), DRAWN_CHECK)
                    return ToolResult(out.replace("check the result with look.", "what the screen shows now:") +
                                      "\nVision: " + seen, tokens=tokens)
                except Exception as exc:  # vision is a bonus here; the typing itself worked
                    log.info("auto look after keys failed: %s", exc)
            return ToolResult(out)
        if name == "hover":
            return ToolResult(browser.hover(args.ref))
        if name == "drag":
            return ToolResult(browser.drag(args.ref, args.to_ref))
        if name == "scroll":
            return ToolResult(browser.scroll(args.direction))
        if name == "back":
            return ToolResult(browser.back())
        if name == "switch_tab":
            return ToolResult(browser.switch_tab(args.index))
        drawn_hint = ("\nThis page draws its content (a canvas, like a spreadsheet grid): cell values are not page "
                      "text. Read them with look.") if snap and any(
            it.get("role") == "canvas" for it in snap.refs.values()) else ""
        if name == "read_page":
            return ToolResult(browser.read_text(args.part) + drawn_hint)
        if name == "find_in_page":
            return ToolResult(browser.find_text(args.words) + drawn_hint)
        if name == "extract":
            return _extract(browser, llm, args.question, drawn_hint, env.task)
        if name == "look":
            if args.file:
                path = F.resolve(args.file, env.task_id)
                if not F.is_image(path):
                    return ToolResult(f"{path.name} is not an image: read it with read_file.", ok=False)
                text, tokens = vision.look_file(llm, path, args.question)
                return ToolResult(f"Vision (the file {path.name}): " + text, tokens=tokens)
            if browser.live_source is None and browser.url in ("", "about:blank"):
                return ToolResult("The browser has no page open (about:blank): there is nothing to look at. To see an "
                                  "image file the person gave you, call look with file=\"<its name>\" (see list_files).",
                                  ok=False)
            text, tokens = vision.look(llm, browser.screenshot(), args.question)
            return ToolResult("Vision: " + text, tokens=tokens)
        if name == "more_tools":
            groups = [g for g in dict.fromkeys(args.groups) if g in GROUPS]
            if "desktop" in groups and not D.available():
                groups.remove("desktop")
                if not groups:
                    return ToolResult("Desktop apps are not available on this computer.", ok=False)
            if not groups:
                return ToolResult("Give one or more groups: " + ", ".join(GROUPS) + ".", ok=False)
            names = set().union(*(GROUPS[g] for g in groups)) - DESKTOP_IN_USE
            return ToolResult("Now you also have: " + ", ".join(sorted(names)) + ".", groups=groups)
        if name == "note":
            return ToolResult(f"Noted: {args.fact}", note={"fact": args.fact, "source": args.source})
        if name == "finish":
            return ToolResult("Finished.", end={"status": "done", "answer": args.answer, "sources": args.sources})
        if name == "give_up":
            return ToolResult("Stopped.", end={"status": "failed", "answer": args.reason, "sources": []})
    except (BrowserError, F.FileError, D.DesktopError) as exc:
        return ToolResult(str(exc), ok=False)
    except Exception as exc:  # unexpected browser/library error: tell the model, keep going
        return ToolResult(f"{name} failed: {type(exc).__name__}: {str(exc).splitlines()[0][:200]}", ok=False)
    return ToolResult(f"Tool {name} is not available.", ok=False)


def _run_desktop(name: str, args: BaseModel, browser: BrowserSession, env: Env) -> ToolResult:
    if name == "desktop_open":
        d = env.open_desktop()
        text = d.open(args.app)
    else:
        d = env.desktop
        if d is None:
            return ToolResult("No app is open. Use desktop_open first.", ok=False)
        if name == "desktop_read":
            text = d.switch(args.window) if args.window else d.read()
        elif name == "desktop_click":
            text = d.click(args.ref)
        elif name == "desktop_type":
            text = d.type(args.ref, args.text, args.enter)
        elif name == "desktop_keys":
            text = d.keys(args.keys)
        else:
            text = d.close_window()
    browser.live_source = d.screenshot if d.windows else None  # the live view shows the app
    return ToolResult(text)


Validator = Callable[[str, dict], Any]
