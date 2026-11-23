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
                           "step_count": state.get("step_count", 0), "checks": 0, "reflections": 0, "feedback": ""}
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
    done = _done_so_far(state)
    if done:
        parts.append("DONE SO FAR (this really happened; never do it again):\n" + "\n".join(f"- {d}" for d in done) +
                     "\nIf this completes the task's action, the next step is finish (a cart: check it once, then "
                     "finish with the item, its price, why it fits, and the other options from your results).")
    recent = (state.get("steps") or [])[-RECENT_STEPS:]
    if recent:
        lines = []
        for s in recent:
            if s.get("tool") in ("recall",):
                continue
            args = {k: v for k, v in (s.get("args") or {}).items() if k != "why"}
            res = (s.get("result") or "").replace("\n", " ")
            res = res[:300] + ("..." if len(res) > 300 else "")
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
                "answer": f"Stopped after {ctx.max_steps} steps without finishing. " + _notes_summary(state)}
    if time.time() - state.get("started", time.time()) > ctx.max_seconds:
        return {"status": "failed", "pending": None,
                "answer": f"Stopped after {ctx.max_seconds // 60} minutes without finishing. " + _notes_summary(state)}

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
            retry = messages + [{"role": "user", "content": "Your last tool call was not valid JSON. Call exactly "
                                 "one tool, with short, valid JSON arguments."}]
            reply = ctx.llm.chat(retry, tools=T.schemas(only, missing), tool_choice="required", max_tokens=2000,
                                 temperature=0.0)
    except LLMError as exc:
        if "invalid tool call" in str(exc):
            return {**_record(ctx, state, {"tool": "model", "args": {}, "why": "", "result": str(exc) +
                                           " Call one tool with valid JSON arguments.", "ok": False,
                                           "url": browser.url}), "pending": None,
                    "feedback": "Your last tool call was invalid JSON. Call exactly one tool with valid arguments."}
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


def _notes_summary(state: AgentState) -> str:
    notes = state.get("notes") or []
    if not notes:
        return ""
    return "What was found so far: " + "; ".join(f"{n['fact']} ({n['source']})" for n in notes[-6:])


def _person_text(state: AgentState) -> str:
    """Everything the person wrote in this task: the request, their answers, and earlier questions in the chat."""
    parts = [state.get("task") or ""]
    parts += [str(s.get("result") or "") for s in state.get("steps") or [] if s.get("tool") == "ask_user"]
    parts += [h.get("request") or "" for h in state.get("history") or []]
    return "\n".join(parts).lower()


ADD_BUTTON = re.compile(r"\badd to (cart|basket|bag|trolley)\b|\badd\b.{0,20}\b(cart|basket)\b", re.I)
ADDED = re.compile(r"\badded to (your |the )?(cart|basket|bag|trolley)\b|\b(cart|basket) \(\d+\)", re.I)
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
    offered = set(p.get("tools") or ctx.tools_now(state))
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
    same = [s for s in recent if s.get("tool") == name and (s.get("args") or {}) == args and s.get("url") == url
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


def check(state: AgentState, config) -> dict:
    ctx = _ctx(config)
    results = "\n".join(f"{s['n']}. {s.get('tool')}: {(s.get('result') or '')[:700]}"
                        for s in (state.get("steps") or [])[-14:] if s.get("tool") not in ("recall",))
    try:
        page = ctx.browser.snapshot().text[:3000]
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
    ok = data.get("ok", True) is not False  # if the checker fails, do not block the answer
    checks = state.get("checks", 0) + 1
    out: dict[str, Any] = {"checks": checks}
    out.update(_record(ctx, state, {"tool": "check", "args": {}, "why": "Check the answer against what was seen",
                                    "result": "Answer OK." if ok else f"Not OK: {data.get('problem')}", "ok": ok,
                                    "url": ctx.browser.url}))
    if ok:
        out["status"] = "done"
        if not state.get("sources"):
            out["sources"] = list(dict.fromkeys(n["source"] for n in state.get("notes") or [] if n.get("source")))[:5]
    elif state.get("salvaged"):  # the answer from the notes was not good enough: stop honestly
        out["status"] = "failed"
        out["answer"] = GIVE_UP + _notes_summary(state)
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
    return {"status": "failed", "answer": GIVE_UP + _notes_summary(state)}


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
