"""The agent as a LangGraph state graph.

recall -> plan -> decide -> gate -> execute -> decide ...      (one tool per step, chosen by the model)
                    |  finish -> check -> learn -> END          (answer checked against what was seen)
                    |  stuck  -> reflect -> decide              (new plan instead of fixed retry rules)
gate pauses the graph with interrupt() for approvals and questions; the runner resumes it with Command(resume=...).

Live objects (browser, model client, callbacks) are not part of the saved state: they live in CONTEXTS,
looked up by the task id (the LangGraph thread_id).
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from webagent import files as F
from webagent.agent import memory, prompts, safety
from webagent.agent import tools as T
from webagent.browser.session import BrowserError, BrowserSession, Snapshot, _domain_match
from webagent.config import settings
from webagent.llm import LLM, LLMError, parse_json

log = logging.getLogger(__name__)

RECENT_STEPS = 14
# The last result of these tools is shown in full (the others are cut to 300 characters).
# (Desktop tools are not here: the app window is shown as the current page instead.)
FULL_RESULT = {"read_page", "find_in_page", "extract", "read_table", "read_file", "list_files", "delegate", "web_search"}
MAX_CHECKS = 2
MAX_REFLECTIONS = 3
FINAL_STEPS = 5                              # the last steps are for writing the answer, not for browsing
FINAL_TOOLS = {"note", "finish", "give_up"}


def _final_stretch(state: dict, max_steps: int) -> bool:
    return state.get("step_count", 0) >= max_steps - FINAL_STEPS


class AgentState(TypedDict, total=False):
    task_id: str
    task: str
    start_url: str
    history: list[dict]       # earlier questions and answers in the same chat
    lessons: list[str]
    plan: list[str]
    notes: list[dict]
    steps: list[dict]         # {n, tool, args, why, result, ok, url}
    step_count: int
    pending: dict | None      # the action the model chose, waiting for gate/execute
    decision: dict | None     # what the person said about the pending action
    feedback: str
    checks: int
    reflections: int
    started: float
    current_url: str
    domains: list[str]
    status: str               # running | done | failed | cancelled
    answer: str
    sources: list[str]
    last_typed: dict | None
    rejected: list[dict]      # actions the person said no to: {key, what}
    repeat_rejects: int       # times the agent tried a rejected action again
    salvaged: bool            # the answer was written from the notes after the agent got stuck
    login_failed: list[str]   # sites whose login the person could not save: do not ask again
    tool_groups: list[str]    # extra tool groups switched on for this task (see tools.GROUPS)
    second_look: bool         # a "nothing new" answer was sent back once for a fresh read


@dataclass
class Ctx:
    task_id: str
    browser: BrowserSession
    llm: LLM
    on_step: Callable[[dict], None] = lambda step: None
    on_tokens: Callable[[int], None] = lambda n: None
    on_plan: Callable[[list[str]], None] = lambda plan: None
    is_cancelled: Callable[[], bool] = lambda: False
    snap: Snapshot | None = None
    max_steps: int = field(default_factory=lambda: settings.max_steps)
    max_seconds: int = field(default_factory=lambda: settings.max_seconds)
    search_url: str = T.SEARCH_URL
    env: T.Env = field(default_factory=T.Env)
    helper: bool = False                       # a helper agent started by delegate
    resume_key: str = ""                       # the action the person decided on while the task was parked

    def __post_init__(self):
        if self.env.task_id == "local":
            self.env.task_id = self.task_id

    def groups_now(self, state: dict) -> set[str]:
        """Tool groups for this turn: chosen for the task, needed by the current page, or files in use."""
        groups = set(state.get("tool_groups") or []) | T.page_groups(self.snap)
        if "files" not in groups and (getattr(self.browser, "downloaded", None) or F.list_files(self.env.task_id)):
            groups.add("files")
        if self.helper:
            groups -= T.HELPER_NO_GROUPS
        return groups

    def tools_now(self, state: dict | None = None) -> set[str]:
        d = self.env.desktop
        groups = None if state is None else self.groups_now(state)
        return T.available(desktop_open=bool(d and d.windows), helper=self.helper, groups=groups)


CONTEXTS: dict[str, Ctx] = {}


def _ctx(config) -> Ctx:
    return CONTEXTS[config["configurable"]["thread_id"]]


def _chat_json(ctx: Ctx, system: str, user: str, max_tokens: int = 900) -> dict:
    try:
        reply = ctx.llm.chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                             json_mode=True, max_tokens=max_tokens, temperature=0.2)
    except LLMError as exc:
        log.warning("JSON call failed: %s", exc)
        return {}
    ctx.on_tokens(reply.tokens)
    return parse_json(reply.text)


def _today() -> str:
    """The model's knowledge stops at its training date; the real date keeps it from calling new things fake."""
    return time.strftime("%d %B %Y")


def _today_line() -> str:
    return (f"TODAY: {_today()}. Websites may show products, versions or events newer than you know: "
            "trust the page, not your memory.")


def _domains_in(text: str) -> list[str]:
    found = re.findall(r"\b((?:[a-z0-9-]+\.)+(?:com|org|net|io|co|edu|gov|jo|uk|de|ai|dev|app|info|me|store|shop)"
                       r"(?:\.[a-z]{2})?)\b", (text or "").lower())
    return list(dict.fromkeys(safety.domain_of(d) for d in found))


def _record(ctx: Ctx, state: AgentState, step: dict) -> dict:
    n = state.get("step_count", 0) + 1
    step = {"n": n, **step}
    ctx.on_step(step)
    steps = (state.get("steps") or []) + [step]
    return {"steps": steps[-60:], "step_count": n}


# ---------------------------------------------------------------- nodes

def recall(state: AgentState, config) -> dict:
    ctx = _ctx(config)
    domains = _domains_in(state["task"])
    if state.get("start_url"):
        domains.insert(0, safety.domain_of(state["start_url"]))
    lessons = [f"[{s['domain']}] {s['text']}" for s in memory.recall(state["task"], domains)]
    out: dict[str, Any] = {"lessons": lessons, "domains": domains, "started": time.time(), "status": "running",
                           "tool_groups": sorted(set(state.get("tool_groups") or []) | T.task_groups(state["task"])),
                           "notes": state.get("notes") or [], "steps": state.get("steps") or [],
                           "step_count": state.get("step_count", 0), "checks": 0, "reflections": 0, "feedback": "",
                           "second_look": False}
    if state.get("start_url"):
        r = safety.domain_reason(state["start_url"])
        if not r:
            try:
                ctx.browser.goto(state["start_url"])
                out["current_url"] = ctx.browser.url
            except BrowserError as exc:
                out["feedback"] = str(exc)
    if lessons:
        out.update(_record(ctx, {**state, **out}, {"tool": "recall", "args": {}, "why": "Lessons from earlier tasks",
                                                   "result": "\n".join(lessons), "ok": True, "url": ""}))
    return out


def plan(state: AgentState, config) -> dict:
    ctx = _ctx(config)
    user = f"TASK: {state['task']}\n{_today_line()}\n"
    if state.get("history"):
        user += "EARLIER IN THIS CHAT:\n" + "\n".join(f"- Q: {h['request']}\n  A: {h['answer']}" for h in state["history"]) + "\n"
    if state.get("lessons"):
        user += "LESSONS:\n" + "\n".join(f"- {l}" for l in state["lessons"]) + "\n"
    if state.get("start_url"):
        user += f"The browser is already at {state['start_url']}\n"
    data = _chat_json(ctx, prompts.PLAN, user, max_tokens=700)
    steps = [str(s)[:200] for s in (data.get("plan") or []) if str(s).strip()][:6] or [
        "Find the right website", "Find the information or do the action", "Check it and answer"]
    ctx.on_plan(steps)
    picked = {str(g).strip().lower() for g in (data.get("tools") or []) if isinstance(g, str)} & set(T.GROUPS)
    return {"plan": steps, "tool_groups": sorted(set(state.get("tool_groups") or []) | picked)}


MILESTONE = re.compile(r"(Added to (?:your |the )?(?:cart|basket|bag|trolley)\b[^|]{0,120}|Message sent[^|]{0,80}|"
                       r"Order (?:placed|confirmed)[^|]{0,80}|Booking confirmed[^|]{0,80}|Thank you for[^|]{0,80}|"
                       r"\"[^\"]{1,60}\" is now inside \"[^\"]{1,60}\")", re.I)


def _done_so_far(state: AgentState) -> list[str]:
    """Progress ledger (Magentic-One style): actions that really happened in this task, from all steps, not only
    the recent ones. Eval: the agent added the right laptop at step 10, compared again for 30 steps, and the checker
    (which saw only the last 14 steps) then said there was no evidence of the cart."""
    out = []
    for s in state.get("steps") or []:
        if s.get("tool") in META_TOOLS or not s.get("ok", True):
            continue
        for m in MILESTONE.finditer(str(s.get("result") or "")):
            line = f"step {s.get('n')}: {m.group(0).strip().rstrip('.')}"
            if not any(m.group(0).strip().rstrip('.') in o for o in out):
                out.append(line)
    return out[-8:]


READ_TOOLS = ("extract", "read_table")
LIST_GOES_ON = "This list goes on"


def _pages_read(state: AgentState, keep: int = 6, cap: int = 1800) -> str:
    """Every list the agent already extracted, in full, so it never has to open a page again to remember it.
    Eval 24 Nov: older results were cut to 300 characters, so after reading page 1 and page 2 the agent no longer
    saw page 1's table, went back "to make sure", then to page 2 again, ~30 times (41 steps, 350k tokens)."""
    steps = state.get("steps") or []
    latest: dict[tuple, dict] = {}
    for s in steps:
        if s.get("tool") in READ_TOOLS and s.get("ok", True) and s.get("result"):
            args = s.get("args") or {}
            latest[(s.get("url") or "", str(args.get("question") or args.get("what") or ""))] = s
    if not latest:
        return ""
    last_n = steps[-1].get("n") if steps else None
    entries = sorted(latest.values(), key=lambda s: s.get("n") or 0)[-keep:]
    blocks, urls = [], []
    for s in entries:
        res = str(s.get("result") or "")
        goes_on = LIST_GOES_ON in res
        res = res.split("\n" + LIST_GOES_ON)[0].split("\nIf the page does show a list")[0].strip()
        urls.append(s.get("url") or "")
        tag = ("the list goes on: there is a Next / Show more page after this one" if goes_on
               else "no Next / Show more here: the end of this list")
        body = "(shown in full below as the LAST RESULT)" if s.get("n") == last_n else (
            res[:cap] + (" ..." if len(res) > cap else ""))
        blocks.append(f"[step {s.get('n')}] {s.get('url')} ({tag}):\n{body}")
    out = ("PAGES YOU ALREADY READ (real results, kept in full; a 'cheapest' or 'best' inside one result is for THAT "
           "page only: compare across all of them). Never open these pages again just to re-read them. If together "
           "they answer the task, note the answer and call finish now (or do the task's action).\n"
           + "\n".join(blocks))
    cur = state.get("current_url") or ""
    if steps and steps[-1].get("tool") not in READ_TOOLS and cur in urls:
        out += (f"\nYou are back on {cur}, a page you already read above: use what you read; do not extract it "
                "again unless the page has changed.")
    return out


# Steps that only move around or look: they never change what a page lists.
_LOOK_ONLY = {"go_to", "back", "switch_tab", "find_in_page", "scroll", "note", "read_page", "look", "extract",
              "read_table", "hover", "check", "reflect", "recall", "model"}


def _read_before(state: AgentState, url: str, question: str) -> int | None:
    """The step that already extracted the same thing from this page, if nothing on the page changed since."""
    steps = state.get("steps") or []
    q = _norm_text(question)
    for i in range(len(steps) - 1, -1, -1):
        s = steps[i]
        if (s.get("tool") == "extract" and s.get("ok", True) and s.get("url") == url
                and _norm_text(str((s.get("args") or {}).get("question") or "")) == q):
            if "Nothing found" in str(s.get("result") or "")[:400]:   # the page may still have been loading
                return None
            later = [t for t in steps[i + 1:] if t.get("url") == url]
            changed = any(t.get("tool") not in _LOOK_ONLY and not (
                t.get("tool") == "click" and str(t.get("result") or "").startswith("Now at")) for t in later) or any(
                t.get("tool") == "check" and "was just reloaded" in str(t.get("result") or "") for t in later)
            return None if changed or _reloaded(state, i, url) else s.get("n")
    return None


def _reloaded(state: AgentState, i: int, url: str) -> bool:
    """The agent opened this page again while already on it (a reload) after step index i, in a task about new
    things (messages, an inbox, "anything new?"): new content may have come. Bug found 24 Nov: the "nothing new"
    check tells the agent to reload the chat and read it again, and the read was refused as "already read"."""
    if not (state.get("second_look") or ABOUT_NEW.search(state.get("task") or "")):
        return False
    steps = state.get("steps") or []
    for j in range(i + 1, len(steps)):
        t = steps[j]
        if t.get("url") != url or not t.get("ok", True) or steps[j - 1].get("url") != url:
            continue
        if t.get("tool") == "go_to" or (t.get("tool") == "click" and str(t.get("result") or "").startswith("Now at")):
            return True
    return False


def _render(state: AgentState, snap_text: str, max_steps: int | None = None) -> str:
    parts = [f"TASK: {state['task']}", _today_line()]
    if state.get("history"):
        parts.append("EARLIER IN THIS CHAT (for follow-up questions):\n" + "\n".join(
            f"- Q: {h['request']}\n  A: {(h.get('answer') or '')[:600]}" for h in state["history"]))
    if state.get("lessons"):
        parts.append("LESSONS FROM EARLIER TASKS:\n" + "\n".join(f"- {l}" for l in state["lessons"]))
    parts.append("PLAN:\n" + "\n".join(f"{i + 1}. {p}" for i, p in enumerate(state.get("plan") or [])))
    if state.get("notes"):
        parts.append("NOTES:\n" + "\n".join(f"- {n['fact']} (source: {n['source']})" for n in state["notes"]))
    answers = _answers(state)
    if answers:
        parts.append("THE PERSON ALREADY ANSWERED (never ask these again; act on the answers):\n" + "\n".join(
            f"- step {n}: you asked \"{q[:300]}{'...' if len(q) > 300 else ''}\" -> they answered \"{a}\""
            for n, q, a in answers[-6:]))
    done = _done_so_far(state)
    if done:
        parts.append("DONE SO FAR (this really happened; never do it again):\n" + "\n".join(f"- {d}" for d in done) +
                     "\nIf this completes the task's action, the next step is finish (a cart: check it once, then "
                     "finish with the item, its price, why it fits, and the other options from your results).")
    recent = (state.get("steps") or [])[-RECENT_STEPS:]
    read = _pages_read(state)
    if read:
        parts.append(read)
    if recent:
        lines = []
        for s in recent:
            if s.get("tool") in ("recall",):
                continue
            args = {k: v for k, v in (s.get("args") or {}).items() if k != "why"}
            res = (s.get("result") or "").replace("\n", " ")
            res = res[:300] + ("..." if len(res) > 300 else "")
            if s.get("tool") == "ask_user":
                # The whole question: cut to 160 characters, "yes" lost what it said yes to (live run: the agent
                # asked for approval of its drafted reply 6 times and never sent it).
                q = str(args.get("question") or "").replace("\n", " ")
                lines.append(f"{s['n']}. ask_user \"{q[:900]}\" -> {'' if s.get('ok', True) else 'ERROR: '}{res}")
                continue
            lines.append(f"{s['n']}. {s.get('tool')} {json.dumps(args, ensure_ascii=False)[:160]} -> "
                         f"{'' if s.get('ok', True) else 'ERROR: '}{res}")
        if lines:
            parts.append("RECENT STEPS:\n" + "\n".join(lines))
    last = recent[-1] if recent else None
    if last and last.get("tool") in FULL_RESULT and last.get("ok"):
        parts.append(f"LAST {last['tool']} RESULT (full):\n<page>\n" + (last.get("result") or "")[:6500] + "\n</page>")
    if state.get("feedback"):
        parts.append("IMPORTANT FEEDBACK: " + state["feedback"])
    used, limit = state.get("step_count", 0), max_steps or settings.max_steps
    parts.append(f"Steps used: {used} of {limit}." + (" Finish soon with what you have." if used > limit * 0.75 else ""))
    if _final_stretch(state, limit):
        parts.append("FINAL STEPS: you are almost out of steps. Browsing is over. Note the facts you saw in the steps "
                     "above if they are not noted yet, then call finish with the best answer from what you found, and "
                     "say clearly what is missing. Only note, finish and give_up are available now.")
    parts.append("CURRENT PAGE:\n<page>\n" + snap_text + "\n</page>")
    parts.append("Call exactly one tool now.")
    return "\n\n".join(parts)


def decide(state: AgentState, config) -> dict:
    ctx = _ctx(config)
    if ctx.is_cancelled():
        return {"status": "cancelled", "answer": "Cancelled by the person.", "pending": None}
    if state.get("step_count", 0) >= ctx.max_steps:
        return {"status": "failed", "pending": None,
                "answer": (f"توقفت بعد {ctx.max_steps} خطوة دون إنهاء المهمة. " if _arabic(state) else
                           f"Stopped after {ctx.max_steps} steps without finishing. ") + _notes_summary(state)}
    if time.time() - state.get("started", time.time()) > ctx.max_seconds:
        return {"status": "failed", "pending": None,
                "answer": (f"توقفت بعد {ctx.max_seconds // 60} دقيقة دون إنهاء المهمة. " if _arabic(state) else
                           f"Stopped after {ctx.max_seconds // 60} minutes without finishing. ") + _notes_summary(state)}

    browser = ctx.browser
    # After a worker restart the browser is new: go back to where the task was.
    if state.get("current_url") and browser.url in ("", "about:blank"):
        try:
            browser.goto(state["current_url"])
        except BrowserError:
            pass
    try:
        snap = browser.snapshot()
        ctx.snap = snap
        snap_text = snap.text
    except BrowserError as exc:
        ctx.snap = None
        snap_text = f"(Could not read the page: {exc})"
    if browser.url in ("", "about:blank"):
        snap_text = "(Empty tab. Use web_search or go_to to start.)"
    desk = ctx.env.desktop
    last_tool = ((state.get("steps") or [{}])[-1] or {}).get("tool")
    if desk is not None and desk.windows and (browser.url in ("", "about:blank") or last_tool in T.DESKTOP):
        # Working in a desktop app: show its window instead of the (unused) browser page.
        try:
            snap_text = ("DESKTOP APP WINDOW (act on it with desktop_click / desktop_type / desktop_keys and these "
                         "d-refs; the browser tools do not work here):\n" + desk.read())
        except Exception as exc:
            snap_text = f"(Could not read the app window: {exc})"

    messages = [{"role": "system", "content": prompts.SYSTEM}, {"role": "user", "content": _render(state, snap_text, ctx.max_steps)}]
    only = FINAL_TOOLS if _final_stretch(state, ctx.max_steps) else ctx.tools_now(state)
    missing = T.missing_groups(ctx.groups_now(state), helper=ctx.helper)
    if not missing:
        only = only - {"more_tools"}
    try:
        try:
            reply = ctx.llm.chat(messages, tools=T.schemas(only, missing), tool_choice="required", max_tokens=2000,
                                 temperature=0.2)
        except LLMError as first:
            if "invalid tool call" not in str(first):
                raise
            # Open models sometimes write broken JSON: ask once more at once (no step spent), more strictly.
            retry = messages + [{"role": "user", "content": _invalid_call_hint(only)}]
            reply = ctx.llm.chat(retry, tools=T.schemas(only, missing), tool_choice="required", max_tokens=2000,
                                 temperature=0.0)
    except LLMError as exc:
        if "invalid tool call" in str(exc):
            hint = _invalid_call_hint(only)
            return {**_record(ctx, state, {"tool": "model", "args": {}, "why": "", "result": str(exc) + " " + hint,
                                           "ok": False, "url": browser.url}), "pending": None, "feedback": hint}
        return {"status": "failed", "answer": str(exc), "pending": None}
    ctx.on_tokens(reply.tokens)
    if not reply.tool_calls:
        text = (reply.text or "").strip()
        return {**_record(ctx, state, {"tool": "model", "args": {}, "why": "", "ok": False, "url": browser.url,
                                       "result": "No tool was called" + (f": {text[:200]}" if text else ".")}),
                "pending": None, "feedback": "You must call exactly one tool (use finish to answer)."}
    call = reply.tool_calls[0]
    return {"pending": {"name": call.name, "args": call.args, "url": browser.url, "tools": sorted(only)},
            "decision": None, "current_url": browser.url}


def _invalid_call_hint(offered: set[str]) -> str:
    """What to say after a tool call Groq refused. In the last steps only note / finish / give_up are offered, and a
    call to any other tool is refused as "invalid". Eval 24 Nov (amazon.sa): told "invalid JSON", the agent tried
    click and find_in_page 3 more times and ran out of steps without an answer."""
    if set(offered) <= FINAL_TOOLS:
        return ("You are out of browsing steps: only note, finish and give_up work now (click, find_in_page and the "
                "other browsing tools are switched off). Call finish now with the best answer from your notes and the "
                "pages you read, and say what is missing.")
    return "Your last tool call was not valid. Call exactly one tool from the list, with short, valid JSON arguments."


def _arabic(state: AgentState) -> bool:
    """The person wrote the task in Arabic: the system's own messages to them are in Arabic too."""
    return len(re.findall(r"[ء-ي]", state.get("task") or "")) >= 3


def _notes_summary(state: AgentState) -> str:
    notes = state.get("notes") or []
    if not notes:
        return ""
    return ("ما تم العثور عليه حتى الآن: " if _arabic(state) else "What was found so far: ") + "; ".join(f"{n['fact']} ({n['source']})" for n in notes[-6:])


YES = re.compile(r"^\W*(yes|yeah|yep|yup|ok|okay|sure|approve[ds]?|i approve|go ahead|send( it)?|do it|confirm(ed)?|"
                 r"agreed?|correct|right|fine|good|perfect|نعم|ايوه|أيوه|ايوا|أجل|اجل|موافق|موافقة|أوافق|اوافق|تمام|"
                 r"اوكي|أوكي|حسنا|حسناً|أكيد|اكيد|طيب|ماشي|أرسلها|ارسلها|أرسله|ارسله|أرسل|ارسل|نفذ|نفّذ|تابع)\b", re.I)
# "لا" only as its own word: as a bare substring it also matched inside other words.
NOT_YES = re.compile(r"\b(no|not|don'?t|do not|never|wait|stop|change|instead|but|لا|لأ|ليس|لكن|بس|انتظر|توقف|"
                     r"غير|غيّر|عدّل|عدل|بدل|بدلا|بدلاً|مش|ما تبعث|لا ترسل)\b", re.I)


def _answers(state: AgentState) -> list[tuple[int, str, str]]:
    """(step, question, answer) for every question the person answered in this task."""
    out = []
    for s in state.get("steps") or []:
        if s.get("tool") == "ask_user" and s.get("ok", True):
            res = str(s.get("result") or "")
            if res.startswith("The person answered:"):
                out.append((s.get("n"), str((s.get("args") or {}).get("question") or ""),
                            res.split(":", 1)[1].strip()[:300]))
    return out


def _is_yes(answer: str) -> bool:
    return bool(YES.search(answer or "")) and not NOT_YES.search(answer or "")


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"\w+", (text or "").lower()) if len(w) > 2}


def _asked_before(state: AgentState, question: str) -> str | None:
    """Refuse a question the person already answered (same question in other words)."""
    new = _words(question)
    if not new:
        return None
    for n, q, a in reversed(_answers(state)):
        old = _words(q)
        if old and len(new & old) / len(new | old) >= 0.5:
            follow = (" They said yes: do it now. If it was a message to send, type exactly that text in the message "
                      "box and send it; the system does not ask again for the text the person approved."
                      if _is_yes(a) else " Act on that answer.")
            return (f"You already asked this at step {n} and the person answered: \"{a}\". Never ask it again."
                    + follow)
    return None


def _norm_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\"'“”‘’`]", "", text or "")).strip().lower()


def _sent_text(state: AgentState, name: str, args) -> str:
    """The text an approval would send: typed with submit=true, or the last text typed before Enter / Send."""
    if name == "type":
        return args.text
    if name == "keys":   # "Hello{Enter}" sends "Hello"; a bare "{Ctrl+Enter}" sends what was typed before
        own = safety.keys_plain_text(args.text)
        if own:
            return own
    for s in reversed(state.get("steps") or []):
        if s.get("tool") in ("type", "keys") and s.get("ok", True):
            return safety.keys_plain_text(str((s.get("args") or {}).get("text") or ""))
    return ""


def _approved_in_chat(state: AgentState, name: str, args, snap: Snapshot | None = None) -> tuple[int, str] | None:
    """The person already approved sending this exact text: the agent showed it in a question and they said yes.
    Then the system does not ask a second time (the person was asked the same thing twice before). Only for the
    action that sends that text: Enter, type with submit, or a Send / Reply / Post button."""
    if name == "click":
        try:
            label = str(snap.element(args.ref).get("name") or "") if snap is not None else ""
        except BrowserError:
            label = ""
        if not re.search(r"\b(send|reply|post|comment|إرسال|ارسال|أرسل|ارسل|رد|الرد|نشر|انشر|تعليق)\b", label, re.I):
            return None
    elif name == "press":
        if args.key != "Enter":
            return None
    elif name == "keys":
        if not (safety.ENTER_KEY.search(args.text) or safety.SEND_KEYS.search(args.text)):
            return None
    elif not (name == "type" and args.submit):
        return None
    text = _norm_text(_sent_text(state, name, args))
    if len(text) < 3:
        return None
    for n, q, a in reversed(_answers(state)):
        if _is_yes(a) and text in _norm_text(q):
            return n, a
    return None


def _person_text(state: AgentState) -> str:
    """Everything the person wrote in this task: the request, their answers, and earlier questions in the chat."""
    parts = [state.get("task") or ""]
    parts += [str(s.get("result") or "") for s in state.get("steps") or [] if s.get("tool") == "ask_user"]
    parts += [h.get("request") or "" for h in state.get("history") or []]
    return "\n".join(parts).lower()


ADD_BUTTON = re.compile(r"\badd to (cart|basket|bag|trolley)\b|\badd\b.{0,20}\b(cart|basket)\b|"
                        r"(أضف|اضف|إضافة|اضافة)\s*(إلى|الى|ل|في)?\s*(ال|ل)?(سلة|عربة|حقيبة)", re.I)
ADDED = re.compile(r"\badded to (your |the )?(cart|basket|bag|trolley)\b|\b(cart|basket) \(\d+\)|"
                   r"(تمت|تم|أضيف|اضيف)\s*(ال)?(إضافة|اضافة)?[^.]{0,30}(السلة|العربة)", re.I)
WANTS_MORE = re.compile(r"\b(two|three|four|five|several|\d+)\s+(of|items?|units?|pieces?|copies|laptops?|of them)\b|"
                        r"\bquantity\b|\beach\b|\ball of\b|\bboth\b", re.I)


def _added_before(state: dict, name: str, args, snap) -> str:
    """A second "Add to Cart" adds a second item (eval: after a failed answer check the agent shopped again and
    the cart had 2 laptops). Refuse it when an item was already added and the task did not ask for more."""
    if name != "click" or snap is None or WANTS_MORE.search(state.get("task") or ""):
        return ""
    el = snap.refs.get(str(getattr(args, "ref", "")).strip()) or {}
    if not ADD_BUTTON.search(str(el.get("name") or "")):
        return ""
    for s in state.get("steps") or []:
        if s.get("tool") == "click" and s.get("ok", True) and ADDED.search(str(s.get("result") or "")):
            return (f"An item was already added to the cart in step {s['n']}. Another click adds a second one, and "
                    "the task asks for one. Do not add again: if a different item is better, say so in your answer "
                    "(the person can change the cart). Check the cart page if you are not sure.")
    return ""


def _breaks_rules(ctx: Ctx, state: dict, name: str, args, snap) -> str:
    """Before "Add to cart": does the item break a rule the task states (brand, price limit, RAM ...)? Eval 24 Nov:
    asked for a Lenovo under $800, the agent never searched for Lenovo and put an HP laptop in the cart; the checker
    saw it only at the end, when the wrong item was already in the cart. Returns the refusal text, or ''."""
    if name != "click" or snap is None:
        return ""
    el = snap.refs.get(str(getattr(args, "ref", "")).strip()) or {}
    if not ADD_BUTTON.search(str(el.get("name") or "")):
        return ""
    item = ""
    reader = getattr(ctx.browser, "item_text", None)
    if callable(reader):
        item = str(reader(str(args.ref).strip()) or "")
    if not item.strip():
        return ""   # nothing to check against: the answer check still looks at the cart at the end
    user = f"TASK: {state.get('task', '')}\n"
    if state.get("history"):
        user += "EARLIER IN THIS CHAT:\n" + "\n".join(f"- Q: {h.get('request', '')}" for h in state["history"]) + "\n"
    said = [str(s.get("result") or "") for s in state.get("steps") or [] if s.get("tool") == "ask_user"]
    if said:
        user += "THE PERSON'S ANSWERS:\n" + "\n".join(f"- {a[:300]}" for a in said) + "\n"
    user += f"ITEM: {item[:1200]}"
    data = _chat_json(ctx, prompts.CART_RULES, user, max_tokens=500)
    if data.get("ok") is not False:
        return ""   # OK, or the check failed: never block on a failed call
    problem = str(data.get("problem") or "it does not meet the task's rules").strip().rstrip(".") + "."
    log.info("Add to cart refused: %s", problem)
    return (f"Not added: this item breaks the task's rules: {problem} Find an item that meets every rule (search the "
            "site for it, for example by brand), check its details, then add that one. If no item meets the rules, "
            "finish and say so.")


def gate(state: AgentState, config) -> dict:
    """Safety check in code. May pause the graph (interrupt) until a person decides."""
    ctx = _ctx(config)
    p = state.get("pending")
    if not p:
        return {}
    try:
        args = T.validate(p["name"], p["args"])
    except ValueError as exc:
        return {"decision": {"refused": str(exc)}}
    if ctx.resume_key:
        # The person decided while no browser was open: the page was opened again. Only go on if the element is
        # the same one they saw; otherwise the agent must look again (and ask again).
        decided_on, ctx.resume_key = ctx.resume_key, ""
        if _action_key(p["name"], args, ctx.snap) != decided_on:
            return {"decision": {"refused": "The page was opened again while waiting for the person and this "
                                            "element is not the same any more. Look at the page and try again."}}
    repeat = _repeat_reason(state, p["name"], p["args"], p.get("url") or "")
    if repeat:
        return {"decision": {"refused": repeat}}
    again = _added_before(state, p["name"], args, ctx.snap)
    if again:
        return {"decision": {"refused": again}}
    wrong = _breaks_rules(ctx, state, p["name"], args, ctx.snap)
    if wrong:
        return {"decision": {"refused": wrong}}
    offered =set(p.get("tools") or ctx.tools_now(state))
    group = next((g for g, names in T.GROUPS.items() if p["name"] in names), "")
    if p["name"] not in offered and group in T.FREE_GROUPS:
        offered.add(p["name"])   # harmless tools from a group: no extra step for more_tools
    if p["name"] not in offered and p["name"] not in FINAL_TOOLS:
        hint = f" Call more_tools with the group {group} first." if group and "more_tools" in offered else ""
        return {"decision": {"refused": f"The tool {p['name']} is not available now.{hint}"}}
    if _final_stretch(state, ctx.max_steps) and p["name"] not in FINAL_TOOLS:
        return {"decision": {"refused": "You are out of browsing steps. Call note for facts you saw, then finish "
                                        "with the best answer from what you found (say what is missing)."}}
    if p["name"] == "login" and safety.domain_of(args.site) in (state.get("login_failed") or []):
        return {"decision": {"refused": f"The person's login for {safety.domain_of(args.site)} could not be saved "
                                        "(some sites refuse to sign in in that window). Do not ask again: continue "
                                        "without the login, or finish and say that the task needs it."}}
    made_up = T.invented_personal(p["name"], args, ctx.snap, _person_text(state))
    if made_up:
        return {"decision": {"refused": made_up}}
    if p["name"] == "ask_user":
        again = _asked_before(state, args.question)
        if again:
            return {"decision": {"refused": again}}
    if p["name"] == "extract":
        n = _read_before(state, ctx.browser.url, args.question)
        if n:
            return {"decision": {"refused": f"You already extracted this from this page at step {n} and the page has "
                                            "not changed since: its result is under PAGES YOU ALREADY READ. Use it. "
                                            "If the pages you read answer the task, note the answer and finish. "
                                            "(If the task is about new content, such as a new message, go_to this "
                                            "page again to reload it, then extract.)"}}
    g = T.gate(p["name"], args, ctx.snap, state.get("last_typed"), desktop=ctx.env.desktop)
    if g.kind == "ok":
        return {"decision": {"approved": True}}
    if g.kind == "refuse":
        return {"decision": {"refused": g.reason}}
    if g.kind == "question":
        action = {"tool": p["name"]}
        if p["name"] == "login":
            action["site"] = safety.domain_of(args.site)  # the dashboard shows an "Open login window" button
            action["url"] = _login_url(args.site, (ctx.snap.url if ctx.snap else "") or state.get("current_url") or "")
        answer = interrupt({"kind": "question", "message": g.reason, "action": action})
        answer = answer if isinstance(answer, dict) else {"text": str(answer or "")}
        return {"decision": {"approved": True, "reply": str(answer.get("text") or "")}, **_pause_out(state, answer)}
    # approval
    what = T.describe_action(p["name"], args, ctx.snap)
    key = _action_key(p["name"], args, ctx.snap)
    earlier = _approved_in_chat(state, p["name"], args, ctx.snap)
    if earlier and not any(r["key"] == key for r in state.get("rejected") or []):
        log.info("Approved earlier by the person (step %s: %s): %s", earlier[0], earlier[1], what)
        return {"decision": {"approved": True, "key": key, "what": what,
                             "note": f"approved at step {earlier[0]} (\"{earlier[1]}\")"}}
    for r in state.get("rejected") or []:
        if r["key"] == key:
            return {"decision": {"refused": f"The person already REJECTED this ({r['what']}). Do not try it again "
                                            "in any way. Call finish now and explain that it was not done because "
                                            "the person said no."},
                    "repeat_rejects": state.get("repeat_rejects", 0) + 1}
    answer = interrupt({"kind": "approval", "message": g.reason,
                        "action": {"tool": p["name"], "args": args.model_dump(exclude={"why"}), "what": what,
                                   "why": args.why, "url": p.get("url"), "key": key}})
    answer = answer if isinstance(answer, dict) else {"approved": bool(answer)}
    return {"decision": {"approved": bool(answer.get("approved")), "note": answer.get("note", ""),
                         "key": key, "what": what}, **_pause_out(state, answer)}


REPEAT_OK = {"scroll", "press", "back", "finish", "give_up", "ask_user"}  # repeating these can be normal
META_TOOLS = {"recall", "check", "reflect", "learn", "model"}


def _ping_pong(recent: list[dict]) -> bool:
    """The last 4 steps only moved between the same two pages (A, B, A, B). Eval 24 Nov: Next / Previous clicked
    ~25 times between page 1 and page 2 without one extract; each click reached a 'new' page, so the same-action
    guard never fired."""
    if len(recent) < 4:
        return False
    moves = all(s.get("ok", True) and (s.get("tool") in ("back", "go_to", "switch_tab") or (
        s.get("tool") == "click" and str(s.get("result") or "").startswith("Now at"))) for s in recent)
    urls = [s.get("url") for s in recent]
    return moves and len(set(urls)) == 2 and urls[0] == urls[2] and urls[1] == urls[3] and urls[0] != urls[1]


def _repeat_reason(state: AgentState, name: str, raw_args: dict | None, url: str) -> str | None:
    """In code, not only in the prompt: the same action on the same page a 3rd time, after it already gave the
    same result twice, cannot help. Refuse it so the model must try something else."""
    if name in REPEAT_OK:
        return None
    args = {k: v for k, v in (raw_args or {}).items() if k != "why"}
    if name == "go_to" and args.get("url"):
        # Back-and-forth loops (open A, back, open A, ...) are not caught by "the same action twice in a row".
        target = str(args["url"]).strip().rstrip("/")
        opened = [s for s in (state.get("steps") or []) if s.get("tool") == "go_to" and s.get("ok", True)
                  and str((s.get("args") or {}).get("url") or "").strip().rstrip("/") == target
                  and not str(s.get("result") or "").startswith("Refused")]
        if len(opened) >= 2:
            return ("You already opened this address twice in this task and it did not get you further. Do not open "
                    "it again: read the page you are on, find the right link or value on it, or finish and say what "
                    "is missing. Never build an address from a value that ends with '…' (it was cut).")
    recent =[s for s in (state.get("steps") or []) if s.get("tool") not in META_TOOLS][-4:]
    if name in ("click", "back", "go_to", "switch_tab") and _ping_pong(recent):
        a, b = recent[-2].get("url"), recent[-1].get("url")
        return (f"You went back and forth between {a} and {b} four times without reading them. Stop moving: "
                "extract what you need from the page you are on (or use PAGES YOU ALREADY READ), note it, then "
                "finish or act.")
    same =[s for s in recent if s.get("tool") == name and (s.get("args") or {}) == args and s.get("url") == url
            and s.get("ok", True) and not str(s.get("result") or "").startswith("Refused")]
    if len(same) >= 2 and same[-1].get("result") == same[-2].get("result"):
        return ("You already did exactly this twice on this page and it changed nothing. Do something different: "
                "another element or option, read_page or find_in_page, note the facts you already have, or finish "
                "with what you found.")
    return None


def _pause_out(state: AgentState, answer: dict) -> dict:
    """Move the start time forward by the time the person needed, so waiting does not use up the time limit."""
    waited = float(answer.get("waited") or 0)
    return {"started": state.get("started", time.time()) + waited} if waited > 0 else {}


def _login_url(site: str, page_url: str) -> str:
    """Where the login window opens: the page the agent is on when it belongs to that site (usually the sign-in
    page itself), otherwise the address the agent gave, otherwise the site's home page."""
    domain = safety.domain_of(site)
    if page_url.startswith(("http://", "https://")) and _domain_match(safety.domain_of(page_url), domain):
        return page_url
    return site if site.lower().startswith(("http://", "https://")) else "https://" + domain


def _action_key(name: str, args, snap: Snapshot | None) -> str:
    """Same action on the same site, whatever its ref is this time."""
    dom = safety.domain_of(snap.url) if snap else ""
    ref = getattr(args, "ref", None)
    if ref and snap and ref in snap.refs:
        el = snap.refs[ref]
        return f"{name}|{el.get('role')}|{(el.get('name') or '').strip().lower()}|{dom}"
    return f"{name}|{getattr(args, 'key', '')}|{dom}"


def execute(state: AgentState, config) -> dict:
    ctx = _ctx(config)
    p, d = state.get("pending"), state.get("decision") or {}
    if not p:
        return {}
    name, raw_args = p["name"], p["args"]
    why = str((raw_args or {}).get("why", ""))[:300]
    base = {"tool": name, "args": {k: v for k, v in (raw_args or {}).items() if k != "why"}, "why": why}
    out: dict[str, Any] = {"pending": None, "decision": None, "feedback": ""}

    if "refused" in d:
        out.update(_record(ctx, state, {**base, "result": "Refused: " + d["refused"], "ok": False, "url": ctx.browser.url}))
        return out
    if not d.get("approved"):
        note = f" The person said: {d['note']}" if d.get("note") else ""
        out.update(_record(ctx, state, {**base, "ok": False, "url": ctx.browser.url,
                                        "result": "The person REJECTED this action. Do not try it again." + note}))
        out["rejected"] = (state.get("rejected") or []) + [{"key": d.get("key", ""), "what": d.get("what", name)}]
        out["feedback"] = ("The person said NO to your last action." + note + " If the task needs that action, do not "
                           "look for another way to do the same thing: call finish and explain that it was not done "
                           "because the person rejected it. Otherwise continue with the rest of the task.")
        return out

    args = T.validate(name, raw_args)
    if name == "ask_user":
        reply = d.get("reply", "")
        out.update(_record(ctx, state, {**base, "result": f"The person answered: {reply}", "ok": True,
                                        "url": ctx.browser.url}))
        return out

    if name == "delegate":
        text, notes = run_helpers(ctx, state, args.tasks)
        out["notes"] = (state.get("notes") or []) + notes
        out.update(_record(ctx, state, {**base, "result": text, "ok": bool(notes), "url": ctx.browser.url}))
        return out
    if name == "login" and d.get("reply", "").strip().lower() in ("skip", "no", "cancel"):
        out.update(_record(ctx, state, {**base, "ok": False, "url": ctx.browser.url,
                                        "result": "The person chose not to log in. Continue without the login, or "
                                                  "finish and say what needs it."}))
        out["login_failed"] = (state.get("login_failed") or []) + [safety.domain_of(args.site)]
        return out
    if ctx.env is not None:
        ctx.env.task = state.get("task", "")
    res = T.run(name, args, ctx.browser, ctx.llm, ctx.snap, ctx.search_url, env=ctx.env)
    if name == "login" and not res.ok:
        out["login_failed"] = (state.get("login_failed") or []) + [safety.domain_of(args.site)]
    if res.tokens:
        ctx.on_tokens(res.tokens)
    if name == "click" and res.ok and ADDED.search(res.text) and not WANTS_MORE.search(state.get("task", "")):
        # eval: after the right laptop was in the cart the agent kept comparing for 24 steps and then answered
        # "nothing meets the criteria". Say what the next steps are.
        res.text += (" The item is in the cart now. Next: open the cart to check it, then finish with that item "
                     "(name, price, why it is the best match, the other options in a few words). Do not shop on.")
    if name == "type" and ctx.snap and args.ref in ctx.snap.refs:
        out["last_typed"] = ctx.snap.refs[args.ref]
    if res.groups:
        out["tool_groups"] = sorted(set(state.get("tool_groups") or []) | set(res.groups))
    if res.note:
        known = {n["fact"].strip().lower() for n in state.get("notes") or []}
        if res.note["fact"].strip().lower() in known:
            res.text = "Already noted. Continue with the next step or finish."
        else:
            out["notes"] = (state.get("notes") or []) + [res.note]
    url = ctx.browser.url
    out["current_url"] = url
    dom = safety.domain_of(url)
    if dom and dom not in (state.get("domains") or []) and not dom.endswith("duckduckgo.com"):
        out["domains"] = (state.get("domains") or []) + [dom]
    out.update(_record(ctx, state, {**base, "result": res.text, "ok": res.ok, "url": url}))
    if res.end:
        out["answer"] = res.end["answer"]
        out["sources"] = res.end.get("sources") or []
        if res.end["status"] == "failed":
            out["status"] = "failed"
    return out


def reflect(state: AgentState, config) -> dict:
    ctx = _ctx(config)
    recent = (state.get("steps") or [])[-10:]
    user = (f"TASK: {state['task']}\n{_today_line()}\nPLAN: {state.get('plan')}\nNOTES: {state.get('notes')}\nRECENT STEPS:\n" +
            "\n".join(f"{s['n']}. {s.get('tool')} {s.get('args')} -> {(s.get('result') or '')[:200]}" for s in recent) +
            f"\nCURRENT PAGE: {ctx.browser.url}")
    if state.get("rejected"):
        user += "\nTHE PERSON REJECTED: " + "; ".join(dict.fromkeys(r["what"] for r in state["rejected"]))
    data = _chat_json(ctx, prompts.REFLECT, user, max_tokens=700)
    new_plan = [str(s)[:200] for s in (data.get("plan") or [])][:5] or state.get("plan") or []
    problem = str(data.get("problem") or "Repeating the same actions without progress.")[:300]
    ctx.on_plan(new_plan)
    out = {"plan": new_plan, "reflections": state.get("reflections", 0) + 1,
           "feedback": f"You were stuck: {problem} Follow the NEW plan and try something different."}
    out.update(_record(ctx, state, {"tool": "reflect", "args": {}, "why": "Stuck: making a new plan",
                                    "result": problem + " New plan: " + " / ".join(new_plan), "ok": True,
                                    "url": ctx.browser.url}))
    return out


# Answers that need the WHOLE list: "the cheapest", "the best", "the only one", "all of them" (English and Arabic).
WHOLE_LIST = re.compile(
    r"\b(cheapest|lowest|least expensive|most expensive|highest|best|top|most|least|only|all|every|none|no other|"
    r"compare|comparison)\b|"
    r"\b(أرخص|الأرخص|اقل|أقل|الأقل|أفضل|افضل|الأفضل|أعلى|الأعلى|أغلى|الأغلى|الوحيد|الوحيدة|كل|جميع|قارن|مقارنة)\b",
    re.I)
# "Nothing new": the answer to a watch or a "did X send anything?" question that was wrong in a live run.
NOTHING_NEW = re.compile(
    r"\b(no|not any|nothing|none)\b[^.\n]{0,40}\b(new|recent|unread|newer)\b|"
    r"\b(has not|hasn't|have not|haven't|did not|didn't)\b[^.\n]{0,30}\b(sent|send|replied|reply|messaged|written|"
    r"write|posted|emailed|answered)\b|"
    r"\bno (new |recent |unread )?(messages?|emails?|mails?|replies|posts|updates|notifications)\b|"
    r"\b(لا|ليس)\b[^.\n]{0,30}\b(جديد|جديدة|رسائل|رسالة)\b|\bلم\s+(يرسل|ترسل|يرد|ترد|يصل|تصل|يكتب|يصلني|تصلني)\b|"
    r"\bلا جديد\b", re.I)
# Tasks where "nothing new" can be a wrong reading of the page: messages, mail, feeds, "anything new?".
ABOUT_NEW = re.compile(
    r"\b(new|newest|latest|recent|unread|last|any|messages?|e?mails?|inbox|chat|replied|reply|sent|send|posted|"
    r"notifications?|watch)\b|رسال|بريد|جديد|آخر|اخر|محادث|دردش|\bرد\b|أرسل|ارسل|إشعار", re.I)
# The answer itself limits a whole-list claim to what was seen ("the cheapest of the first 20 results").
LIMITED = re.compile(r"\b(first|top) \d+ (results|items|products|listings)|\bon (the first|page 1|this) page\b|"
                     r"\b(of|among) the (\d+ )?(results|items|products) (shown|seen|on (the first|this) page)\b|"
                     r"في الصفحة الأولى|أول \d+ (نتيجة|نتائج|منتج)", re.I)


def _same_list(a: str, b: str) -> bool:
    """Two addresses of one list: the same site and path (page=2, ?start=20 ...)."""
    from urllib.parse import urlparse
    pa, pb = urlparse(a or ""), urlparse(b or "")
    return bool(pa.netloc) and pa.netloc == pb.netloc and pa.path == pb.path and a != b


def _list_not_followed(state: AgentState) -> str:
    """The address of a list that the extract tool said goes on (Next / Show more) and that the agent never went
    past. Eval 24 Nov: "the cheapest laptop" was answered from page 1; page 2 had a cheaper one."""
    steps = state.get("steps") or []
    open_list = ""
    for i, s in enumerate(steps):
        if s.get("tool") != "extract" or "This list goes on" not in str(s.get("result") or ""):
            continue
        u = s.get("url") or ""
        # Another page of the same list read EARLIER counts too (eval 24 Nov: page 2 was read first, then page 1,
        # and page 1's "goes on" was flagged although every page had been read).
        followed = any(t.get("tool") in READ_TOOLS and t.get("ok", True) and _same_list(u, t.get("url") or "")
                       for t in steps[:i])
        for t in steps[i + 1:]:
            res = str(t.get("result") or "")
            if not t.get("ok", True):
                continue
            if _same_list(u, t.get("url") or ""):                                  # opened page 2, 3 ...
                followed = True
            elif t.get("tool") == "extract" and t.get("url") == u and "This list goes on" not in res:
                followed = True                                                   # the list ended here
            elif t.get("tool") == "click" and t.get("url") == u and res.startswith("New text on the page"):
                followed = True                                                   # "Show more" loaded more
        if not followed:
            open_list = u
    return open_list


def _code_check(state: AgentState) -> tuple[dict, dict]:
    """Checks in code before the model's check: (problem as the checker would return it, extra state)."""
    answer = state.get("answer") or ""
    if state.get("salvaged"):
        return {}, {}
    if (NOTHING_NEW.search(answer) and not state.get("second_look") and ABOUT_NEW.search(state.get("task") or "")
            and not state.get("rejected")):   # "not sent because you said no" is not a reading of the page
        return ({"ok": False, "fix": "work",
                 "problem": "Before you report that nothing new came, look once more: open the conversation or inbox "
                            "again (go_to its address to reload it), read its NEWEST part (the bottom of a chat, the top "
                            "of an inbox; 'Text in view now' shows the screen) and name the newest item you see with its "
                            "time and sender. Only then answer."}, {"second_look": True})
    open_list = _list_not_followed(state)
    if open_list and WHOLE_LIST.search(f"{state.get('task', '')} {answer}") and not LIMITED.search(answer):
        return ({"ok": False, "fix": "work",
                 "problem": f"The list at {open_list} goes on (the extract said it has a Next / Show more control) but "
                            "only its first part was read, and the task or the answer is about the whole list (the "
                            "cheapest, the best, the only one, all). Open the next page, extract again, and answer from "
                            "every page."}, {})
    return {}, {}


# The checker saying an action HAPPENED ("the agent added an item to the cart", "the message was sent").
CLAIMED_ACTION = re.compile(
    r"\b(added|adds|adding)\b[^.]{0,40}\b(cart|basket|bag)\b|\b(was|were|has been|have been) (sent|submitted|posted|"
    r"ordered|booked|purchased|bought|deleted)\b|\b(placed|submitted|sent|booked|purchased|bought|posted) (an?|the)\b",
    re.I)
_READ_ONLY = {"extract", "read_page", "find_in_page", "read_table", "read_file", "list_files", "look", "web_search",
              "go_to", "back", "scroll", "switch_tab", "note", "hover", "finish", "give_up", "ask_user"}


def _claims_unseen_action(state: AgentState, problem: str) -> bool:
    """True when the checker says an action happened but no step shows it. Eval 24 Nov, twice: "the agent added an
    item to the cart (step 3)" about an extract step, with "Cart (0)" on the page; the right answer got a false
    warning. An action the agent really did leaves a result: "Added to Cart", "Sent", a click or key step."""
    if not CLAIMED_ACTION.search(problem):
        return False
    if _done_so_far(state):
        return False
    acts = [s for s in state.get("steps") or [] if s.get("ok", True) and s.get("tool") not in _READ_ONLY
            and s.get("tool") not in META_TOOLS]
    # only navigation clicks ("Now at ...") and ticked filters: nothing was added, sent or bought
    return all(s.get("tool") == "click" and re.match(r"(Now at|e\d+ \"[^\"]*\" is now (checked|unchecked))",
                                                       str(s.get("result") or "")) for s in acts)


VIEW_MARK = "Text in view now"


def _page_for_check(text: str, view_cap: int = 1500, cap: int = 3000) -> str:
    """The page for the checker: what is on screen FIRST, then the start of the element list. Eval 24 Nov: the
    checker got the first 3000 characters, the on-screen text (a chat's newest message) comes last and was cut, so
    it rejected a right answer twice ("the page shows no such reply")."""
    i = text.find(VIEW_MARK)
    if i < 0:
        return text[:cap]
    view = text[i:i + view_cap]
    return view + "\n\nPAGE ELEMENTS (start):\n" + text[:max(0, cap - len(view))]


def _reload_for_second_look(ctx: Ctx, state: AgentState) -> str:
    """Open again the page the "nothing new" answer was read from (the last page read), so new messages show.
    Returns its address, or '' if it could not be opened."""
    url = ""
    for s in reversed(state.get("steps") or []):
        if s.get("tool") in ("read_page", "extract", "find_in_page", "read_table", "look", "go_to") and s.get("url"):
            url = s["url"]
            break
    url = url or ctx.browser.url
    if not url.startswith(("http://", "https://")):
        return ""
    try:
        ctx.browser.goto(url)
        ctx.snap = None
        return url
    except Exception as exc:   # BrowserError, or a test double without a browser
        log.info("Reload for the second look failed: %s", exc)
        return ""


def check(state: AgentState, config) -> dict:
    ctx = _ctx(config)
    code_problem, extra = _code_check(state)
    if code_problem:
        out: dict[str, Any] = {**extra, "checks": state.get("checks", 0) + (0 if "second_look" in extra else 1)}
        if "second_look" in extra:
            # Reload in code, not by asking the model. Eval 24 Nov: told to reload the chat, the agent only scrolled
            # and sent the same "no reply" answer again, and the checker passed it (the reply came after a reload).
            reloaded = _reload_for_second_look(ctx, state)
            if reloaded:
                code_problem = {**code_problem, "problem": (
                    f"Before you report that nothing new came, look once more. The page {reloaded} was just reloaded "
                    "for you: read its NEWEST part now ('Text in view now': the bottom of a chat, the top of an inbox) "
                    "and name the newest item with its time and sender. Notes written before this reload may be out "
                    "of date: answer from what the page shows now.")}
                out["current_url"] = ctx.browser.url
        out.update(_record(ctx, state, {"tool": "check", "args": {}, "why": "Check the answer against what was seen",
                                        "result": f"Not OK: {code_problem['problem']}", "ok": False,
                                        "url": ctx.browser.url}))
        if out["checks"] >= MAX_CHECKS and "second_look" not in extra:
            out["status"] = "done"
            out["answer"] = (state.get("answer") or "") + f"\n\n(Note: not checked fully: {code_problem['problem']})"
        else:
            out["feedback"] = f"Your answer was checked and is not ready: {code_problem['problem']}"
            out["answer"] = ""
        return out
    results ="\n".join(f"{s['n']}. {s.get('tool')}: {(s.get('result') or '')[:700]}"
                        for s in (state.get("steps") or [])[-14:] if s.get("tool") not in ("recall",))
    try:
        page = _page_for_check(ctx.browser.snapshot().text)
    except Exception:
        page = "(not available)"
    user = (f"TASK: {state['task']}\n{_today_line()}\n\nANSWER: {state.get('answer')}\nSOURCES: {state.get('sources')}\n\n"
            f"NOTES: {json.dumps(state.get('notes') or [], ensure_ascii=False)[:3000]}\n\n"
            f"DONE IN THIS TASK (from all steps, real results): {'; '.join(_done_so_far(state)) or 'nothing recorded'}\n\n"
            f"STEP RESULTS (the last ones):\n{results}\n\n"
            f"CURRENT PAGE (what the browser shows now):\n{page}")
    if state.get("history"):
        user = "EARLIER IN THIS CHAT:\n" + "\n".join(f"Q: {h['request']} A: {h['answer']}" for h in state["history"]) + "\n\n" + user
    data = _chat_json(ctx, prompts.CHECK, user, max_tokens=600)
    if "ok" not in data:   # the checker call failed or said nothing: try once more
        data = _chat_json(ctx, prompts.CHECK, user, max_tokens=600)
    unchecked = "ok" not in data
    ok = data.get("ok", True) is not False
    if not ok and _claims_unseen_action(state, str(data.get("problem") or "")):
        log.info("Checker claimed an action no step shows; ignored: %s", data.get("problem"))
        data, ok = {"ok": True}, True
    checks = state.get("checks", 0) + 1
    out: dict[str, Any] = {"checks": checks}
    out.update(_record(ctx, state, {"tool": "check", "args": {}, "why": "Check the answer against what was seen",
                                    "result": ("Could not run the check." if unchecked else "Answer OK.") if ok
                                    else f"Not OK: {data.get('problem')}", "ok": ok, "url": ctx.browser.url}))
    if ok:
        out["status"] = "done"
        if unchecked:   # never pass an unchecked answer off as checked
            out["answer"] = (state.get("answer") or "") + "\n\n(Note: this answer could not be checked automatically.)"
        if not state.get("sources"):
            out["sources"] = list(dict.fromkeys(n["source"] for n in state.get("notes") or [] if n.get("source")))[:5]
    elif state.get("salvaged"):  # the answer from the notes was not good enough: stop honestly
        out["status"] = "failed"
        out["answer"] = _give_up_text(state)
    elif checks >= MAX_CHECKS:
        out["status"] = "done"
        out["answer"] = (state.get("answer") or "") + f"\n\n(Note: the automatic check was not fully satisfied: {data.get('problem')})"
    elif data.get("fix") == "answer":
        # The work is done; only the words are missing. Do not browse again (eval: the agent shopped again after
        # such a check and put a second laptop in the cart).
        out["feedback"] = (f"Your answer was checked: {data.get('problem')} The work itself is done. Call finish "
                           "again now with a better answer, using only what you already found (notes, step results). "
                           "Do not repeat any action.")
        out["answer"] = ""
    else:
        out["feedback"] = (f"Your answer was checked and is not ready: {data.get('problem')} "
                           "Find what is missing on the web, then call finish again. Do not repeat actions that "
                           "already happened (an item already in the cart, a form already sent).")
        out["answer"] = ""
    return out


def learn(state: AgentState, config) -> dict:
    ctx = _ctx(config)
    domains = [d for d in (state.get("domains") or []) if d]
    # Only a task that really succeeded teaches anything: lessons from failed attempts taught the agent its own
    # wrong habits (eval: "type the data in the formula bar" after a failed spreadsheet run).
    if not domains or state.get("status") != "done" or state.get("salvaged"):
        return {}
    steps = "\n".join(f"{s['n']}. {s.get('tool')} {s.get('args')} @ {s.get('url')} -> {(s.get('result') or '')[:150]}"
                      for s in (state.get("steps") or [])[-25:])
    data = _chat_json(ctx, prompts.LEARN, f"TASK: {state['task']}\nRESULT: {state.get('status')}\nWEBSITES: {domains}\n"
                                          f"STEPS:\n{steps}", max_tokens=700)
    saved = []
    for item in (data.get("lessons") or [])[:3]:
        if not isinstance(item, dict):
            continue
        dom = safety.domain_of(str(item.get("domain", "")))
        lesson = str(item.get("lesson", ""))[:220]
        if dom and lesson:
            memory.remember(dom, lesson)
            saved.append(f"[{dom}] {lesson}")
    if saved:
        return _record(ctx, state, {"tool": "learn", "args": {}, "why": "Save lessons for next time",
                                    "result": "\n".join(saved), "ok": True, "url": ""})
    return {}


# ---------------------------------------------------------------- routing

def _same(a: dict, b: dict) -> bool:
    return a.get("tool") == b.get("tool") and a.get("args") == b.get("args")


def _stuck(state: AgentState) -> bool:
    all_steps = state.get("steps") or []
    # Only judge the steps since the last new plan: the new plan needs room to work.
    last_reflect = max((i for i, s in enumerate(all_steps) if s.get("tool") == "reflect"), default=-1)
    steps = [s for s in all_steps[last_reflect + 1:] if s.get("tool") not in ("recall", "check", "reflect", "learn")]
    if len(steps) >= 3 and _same(steps[-1], steps[-2]) and _same(steps[-2], steps[-3]):
        # The same click 3 times is fine while every click brings something new ("Show more" loading the next
        # reviews; eval: called a loop, the agent stopped early and counted 2 of 4 one-star reviews).
        if len({str(s.get("result") or "")[:400] for s in steps[-3:]}) < 3:
            return True
    if len(steps) >= 4 and not any(s.get("ok") for s in steps[-4:]):
        return True
    # 8 steps on one page, no note, and the results keep repeating: scrolling/reading in circles.
    # (Different results mean progress, e.g. pressing calculator buttons changes the display.)
    if len(steps) >= 8:
        last = steps[-8:]
        if (len({s.get("url") for s in last}) == 1 and not any(s.get("tool") == "note" for s in last)
                and len({(s.get("result") or "")[:400] for s in last}) <= 4):
            return True
    return False


def after_decide(state: AgentState) -> str:
    if state.get("status") in ("failed", "cancelled"):
        return "learn"
    if not state.get("pending"):
        return "decide" if state.get("step_count", 0) < settings.max_steps + 2 else "learn"
    return "gate"


def after_execute(state: AgentState) -> str:
    if state.get("status") == "failed":
        return "learn"
    if state.get("repeat_rejects", 0) >= 2:
        return "stop_rejected"
    if state.get("answer") and state.get("status") == "running" and (state.get("steps") or [{}])[-1].get("tool") == "finish":
        return "check"
    if _stuck(state):
        if state.get("reflections", 0) >= MAX_REFLECTIONS:
            return "give_up"
        last = (state.get("steps") or [])[-1]
        if last.get("tool") != "reflect":
            return "reflect"
    return "decide"


GIVE_UP = "The agent got stuck and stopped after several new plans. "
GIVE_UP_AR = "توقف الوكيل لأنه لم يتقدم رغم عدة خطط جديدة. "


def _give_up_text(state: AgentState) -> str:
    return (GIVE_UP_AR if _arabic(state) else GIVE_UP) + _notes_summary(state)


def give_up(state: AgentState, config) -> dict:
    """Out of ideas. If the notes already answer the task, write the answer from them (the checker still decides)."""
    notes = state.get("notes") or []
    if notes:
        ctx = _ctx(config)
        notes_text = "\n".join(f"- {n['fact']} (source: {n['source']})" for n in notes)
        data = _chat_json(ctx, prompts.SALVAGE, f"TASK: {state['task']}\n\nNOTES:\n{notes_text}", max_tokens=600)
        answer = str(data.get("answer") or "").strip()
        if answer:
            return {"answer": answer, "salvaged": True, "status": "running",
                    "sources": list(dict.fromkeys(n["source"] for n in notes if n.get("source")))[:5]}
    return {"status": "failed", "answer": _give_up_text(state)}


def after_give_up(state: AgentState) -> str:
    return "check" if state.get("salvaged") and state.get("status") == "running" else "learn"


def stop_rejected(state: AgentState, config) -> dict:
    whats = "; ".join(dict.fromkeys(r["what"] for r in state.get("rejected") or []))
    return {"status": "done", "answer": f"Not done: you rejected this action, so I stopped ({whats}). " + _notes_summary(state)}


def after_check(state: AgentState) -> str:
    return "learn" if state.get("status") in ("done", "failed") else "decide"


HELPER_APPROVAL = {"approved": False, "note": "You are a helper agent: you only look things up. Do not do this; "
                                             "finish with what you found."}
HELPER_REPLY = {"text": "Nobody can answer a helper agent. Continue with what you have, or finish."}


def run_helpers(ctx: Ctx, state: AgentState, tasks: list[str]) -> tuple[str, list[dict]]:
    """delegate: each sub-task is done by a helper agent (the same graph, its own browser tabs, no risky actions).
    They run one after another in this worker; their steps appear in the task's trace as "helper N: ..."."""
    from langgraph.checkpoint.memory import MemorySaver

    if ctx.helper:
        return "Helpers cannot start helpers.", []
    tasks = [t.strip() for t in tasks if t and t.strip()][: settings.helpers]
    if not tasks:
        return "Give 2 to 4 sub-tasks.", []
    lines, notes = [], []
    call = state.get("step_count", 0) + 1
    for i, sub_task in enumerate(tasks, 1):
        hid = f"{ctx.task_id}-s{call}h{i}"
        child = None
        try:
            child = ctx.browser.child(hid)
            ctx.browser.live_source = child.screenshot  # the live view follows the helper
            hctx = Ctx(task_id=hid, browser=child, llm=ctx.llm, helper=True,
                       on_step=lambda st, i=i: ctx.on_step({**st, "tool": f"helper {i}: {st.get('tool')}"}),
                       on_tokens=ctx.on_tokens, is_cancelled=ctx.is_cancelled, search_url=ctx.search_url,
                       max_steps=settings.helper_steps, max_seconds=min(ctx.max_seconds, 300),
                       env=T.Env(task_id=ctx.task_id))
            CONTEXTS[hid] = hctx
            graph = build_graph(MemorySaver())
            cfg = {"configurable": {"thread_id": hid}, "recursion_limit": 400}
            res = graph.invoke({"task_id": hid, "task": sub_task, "start_url": "", "history": []}, cfg)
            while res.get("__interrupt__") and not ctx.is_cancelled():
                kind = (res["__interrupt__"][0].value or {}).get("kind")
                res = graph.invoke(Command(resume=HELPER_APPROVAL if kind == "approval" else HELPER_REPLY), cfg)
            status, answer = res.get("status") or "failed", (res.get("answer") or "").strip()
            sources = res.get("sources") or []
        except Exception as exc:  # one helper failing must not stop the others
            log.exception("Helper %s crashed", hid)
            status, answer, sources = "failed", f"The helper stopped: {type(exc).__name__}", []
        finally:
            CONTEXTS.pop(hid, None)
            ctx.browser.live_source = None
            if child is not None:
                child.close()
        lines.append(f"Helper {i} ({sub_task}): {status.upper()}: {answer[:900]}" +
                     (f" Sources: {', '.join(sources[:3])}" if sources else ""))
        if status == "done" and answer:
            notes.append({"fact": f"{sub_task} -> {answer[:600]}", "source": (sources or ["helper agent"])[0]})
        if ctx.is_cancelled():
            break
    return "\n".join(lines), notes


def build_graph(checkpointer=None):
    g = StateGraph(AgentState)
    g.add_node("recall", recall)
    g.add_node("plan", plan)
    g.add_node("decide", decide)
    g.add_node("gate", gate)
    g.add_node("execute", execute)
    g.add_node("reflect", reflect)
    g.add_node("check", check)
    g.add_node("learn", learn)
    g.add_node("give_up", give_up)
    g.add_node("stop_rejected", stop_rejected)
    g.add_edge(START, "recall")
    g.add_edge("recall", "plan")
    g.add_edge("plan", "decide")
    g.add_conditional_edges("decide", after_decide, ["gate", "decide", "learn"])
    g.add_edge("gate", "execute")
    g.add_conditional_edges("execute", after_execute, ["check", "reflect", "decide", "learn", "give_up", "stop_rejected"])
    g.add_edge("reflect", "decide")
    g.add_conditional_edges("check", after_check, ["learn", "decide"])
    g.add_conditional_edges("give_up", after_give_up, ["check", "learn"])
    g.add_edge("stop_rejected", "learn")
    g.add_edge("learn", END)
    return g.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------- checkpointer

_saver = None


def checkpointer():
    """SqliteSaver locally, PostgresSaver in production (same DATABASE_URL)."""
    global _saver
    if _saver is not None:
        return _saver
    if settings.is_postgres:
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg_pool import ConnectionPool

        url = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
        pool = ConnectionPool(url, max_size=5, open=True, kwargs={"autocommit": True, "prepare_threshold": 0})
        _saver = PostgresSaver(pool)
        _saver.setup()
    else:
        import sqlite3

        from langgraph.checkpoint.sqlite import SqliteSaver

        path = settings.database_url.replace("sqlite:///", "")
        cp_path = str(path).replace(".db", "_checkpoints.db")
        conn = sqlite3.connect(cp_path, check_same_thread=False)
        _saver = SqliteSaver(conn)
        _saver.setup()
    return _saver
