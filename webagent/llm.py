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
                # Model wrote a bad tool call: Groq returns 400 tool_use_failed. Retry once more, then give up.
                retryable = status in (408, 409, 429, 500, 502, 503, 504) or type(exc).__name__ in (
                    "APIConnectionError", "APITimeoutError", "RateLimitError", "InternalServerError") or (
                    status == 400 and "tool_use_failed" in body)
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
