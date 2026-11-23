"""Groq client with retries and clear error messages.

`LLM` is a small interface so tests can swap in a scripted fake model.
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from webagent.config import settings

log = logging.getLogger(__name__)


class LLMError(Exception):
    """The model service failed in a way retrying will not fix. The message is safe to show."""


@dataclass
class ToolCall:
    name: str
    args: dict
    id: str = "call_0"


@dataclass
class Reply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tokens: int = 0


class LLM(Protocol):
    def chat(self, messages: list[dict], *, tools: list[dict] | None = None, tool_choice: str | None = None,
             json_mode: bool = False, model: str | None = None, max_tokens: int = 1500,
             temperature: float = 0.2) -> Reply: ...


def _friendly(exc: Exception) -> str:
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    if status == 401 or name == "AuthenticationError":
        return "The Groq key was refused. Set a valid GROQ_API_KEY."
    if status == 404:
        return "The model was not found on Groq. Check WA_MODEL / WA_VISION_MODEL."
    if status == 413:
        return "The request to the model was too large."
    if status == 429 or name == "RateLimitError":
        return "Groq rate limit reached. Try again in a minute."
    if name in ("APIConnectionError", "APITimeoutError"):
        return "Could not reach Groq (network problem)."
    return f"The model call failed ({name}{' ' + str(status) if status else ''})."


def _failed_generation(exc: Exception) -> str:
    """The text the model wrote when Groq refused its tool call (400 tool_use_failed)."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict) and err.get("failed_generation"):
            return str(err["failed_generation"])
    m = re.search(r"'failed_generation': '(.*?)'(?:,|\})", str(exc), re.S)
    return m.group(1) if m else ""


def recover_tool_call(text: str, tool_names: set[str]) -> ToolCall | None:
    """Rebuild the tool call the model meant from the text it wrote. gpt-oss sometimes writes the call in its own
    channel format or wraps it as {"name": ..., "arguments": ...}; a live Gmail run lost 4 steps (and minutes of
    retries) to such calls."""
    if not text or not tool_names:
        return None
    dec = json.JSONDecoder()
    objs = []
    for i, ch in enumerate(text):
        if ch == "{":
            try:
                obj, _ = dec.raw_decode(text[i:])
            except ValueError:
                continue
            if isinstance(obj, dict):
                objs.append(obj)
    named = re.search(r"(?:to=)?functions\.(\w+)", text)
    for obj in objs:
        name = obj.get("name") or obj.get("tool") or obj.get("function")
        args = obj.get("arguments", obj.get("parameters", obj.get("args")))
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = None
        if isinstance(name, str) and name in tool_names and isinstance(args, dict):
            return ToolCall(name=name, args=args)
    if named and named.group(1) in tool_names and objs:
        return ToolCall(name=named.group(1), args=objs[0])
    return None


class GroqLLM:
    def __init__(self, api_key: str | None = None, model: str | None = None, retries: int = 4):
        from groq import Groq

        key = api_key or settings.groq_api_key
        if not key:
            raise LLMError("GROQ_API_KEY is not set.")
        self.client = Groq(api_key=key, timeout=60, max_retries=0)
        self.model = model or settings.model
        self.retries = retries

    def chat(self, messages, *, tools=None, tool_choice=None, json_mode=False, model=None, max_tokens=1500,
             temperature=0.2) -> Reply:
        kwargs: dict[str, Any] = {"model": model or self.model, "messages": messages, "temperature": temperature,
                                  "max_completion_tokens": max_tokens}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"
            kwargs["parallel_tool_calls"] = False
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if (model or self.model).startswith("openai/gpt-oss"):
            kwargs["reasoning_effort"] = "medium"
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = self.client.chat.completions.create(**kwargs)
                msg = resp.choices[0].message
                calls = []
                for tc in msg.tool_calls or []:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {"_raw": tc.function.arguments}
                    calls.append(ToolCall(name=tc.function.name, args=args if isinstance(args, dict) else {},
                                          id=tc.id))
                tokens = getattr(resp.usage, "total_tokens", 0) or 0
                return Reply(text=msg.content or "", tool_calls=calls, tokens=tokens)
            except Exception as exc:  # groq.* errors
                last = exc
                status = getattr(exc, "status_code", None)
                body = str(exc)
                bad_call = status == 400 and "tool_use_failed" in body
                if bad_call:
                    # Model wrote a bad tool call: rebuild what it meant, else retry only once (the same request
                    # usually fails the same way; 4 backed-off retries cost about a minute each time).
                    text = _failed_generation(exc)
                    names = {t.get("function", {}).get("name") for t in tools or []}
                    call = recover_tool_call(text, names)
                    if call:
                        log.info("Recovered a malformed tool call: %s", call.name)
                        return Reply(text="", tool_calls=[call], tokens=0)
                    log.warning("Model wrote an invalid tool call: %s", text[:300].replace("\n", " "))
                    if attempt >= 1:
                        break
                retryable = status in (408, 409, 429, 500, 502, 503, 504) or type(exc).__name__ in (
                    "APIConnectionError", "APITimeoutError", "RateLimitError", "InternalServerError") or bad_call
                if not retryable or attempt == self.retries:
                    break
                wait = min(30.0, 2 ** attempt + random.random())
                m = re.search(r"try again in ([\d.]+)s", body)
                if m:
                    wait = min(60.0, float(m.group(1)) + 0.5)
                log.warning("Groq call failed (%s), retry %s in %.1fs", type(exc).__name__, attempt + 1, wait)
                time.sleep(wait)
        if last is not None and getattr(last, "status_code", None) == 400 and "tool_use_failed" in str(last):
            # Recover the model's intent when it printed the call as text.
            raise LLMError("The model produced an invalid tool call.")
        raise LLMError(_friendly(last)) from last


def parse_json(text: str) -> dict:
    """Read a JSON object from a model reply, even inside ``` fences or with text around it."""
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        data = json.loads(text[start:end + 1])
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}
