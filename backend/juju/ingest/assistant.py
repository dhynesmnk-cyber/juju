"""The live chat's assistant: a model that can ask the server for live prices and stats, and word
the answer (docs/plans/live-chat.md, step 3). The model's words are never trusted here: the caller
checks them (`core/assistant.py`) and falls back to the template sentences when they fail.

One question is a short loop against an OpenAI-format chat completions API (DeepSeek Flash:
`LLM_BASE_URL=https://api.deepseek.com`, `LLM_MODEL=deepseek-flash`, thinking turned off through
`LLM_EXTRA_BODY`, since with tools a thinking model's reasoning must be passed back):

- the model answers, or asks for tools; each tool call is run by the caller's `Tools` and its
  result goes back as a `tool` message; at most four calls are run for a question, and after
  that the model is told to answer with what it has;
- the whole question has 15 seconds, and a daily budget of questions (`Budget`, the parse
  fallback's counter, counting messages rather than model calls);
- no failure raises: a spent budget, a timeout, 402 (no balance), 429 (too many at once), a bad
  reply each end the question with a status, and the caller carries on without the model.

Nothing is logged but the kind of failure: not the words, and never the key.
"""
import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from juju.core.assistant import MAX_TOOL_CALLS, as_json, tool_specs
from juju.ingest.llm import Budget

log = logging.getLogger("juju.assistant")

DEADLINE_SECONDS = 15.0
CALL_SECONDS = 12.0          # one model call; the question's deadline may be nearer
MAX_ROUNDS = 5               # model calls for one question
MAX_TOKENS = 400
TOO_MANY = "Tool limit reached for this question. Answer with what you already have."
BAD_ARGUMENTS = "The arguments were not valid JSON for this tool."
TOOL_FAILED = "That tool failed. Answer with what you already have."


class Tools(Protocol):
    """What the question's tools are: run by the caller, who owns the credits and the data."""

    def call(self, name: str, arguments: dict[str, Any]) -> dict: ...


@dataclass
class Trace:
    name: str
    arguments: dict[str, Any]
    result: dict


@dataclass
class Outcome:
    text: str | None          # the model's last words, unchecked; None if it never gave any
    status: str               # ok, budget, timeout, balance, rate_limited, error, rounds
    calls: list[Trace] = field(default_factory=list)
    rounds: int = 0


class Assistant:
    def __init__(self, api_key: str, base_url: str, model: str, budget: Budget, *,
                 extra_body: dict[str, Any] | None = None, client: httpx.Client | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self._key = api_key
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._budget = budget
        self._extra = dict(extra_body or {})
        self._client = client or httpx.Client(timeout=CALL_SECONDS)
        self._clock = clock

    def run(self, system: str, history: Sequence[tuple[str, str]], message: str,
            tools: Tools) -> Outcome:
        """One question. `history`: earlier (role, text) turns, "user" or "assistant"."""
        if not self._budget.take():
            log.warning("the assistant's budget for today is used up")
            return Outcome(None, "budget")
        deadline = self._clock() + DEADLINE_SECONDS
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        messages += [{"role": role, "content": text} for role, text in history
                     if role in ("user", "assistant") and text]
        messages.append({"role": "user", "content": message})
        out = Outcome(None, "rounds")
        run_calls = 0
        for _ in range(MAX_ROUNDS):
            remaining = deadline - self._clock()
            if remaining <= 0.5:
                out.status = "timeout"
                return out
            body = {**self._extra, "model": self._model, "messages": messages,
                    "temperature": 0, "max_tokens": MAX_TOKENS, "tools": tool_specs(),
                    "tool_choice": "auto" if run_calls < MAX_TOOL_CALLS else "none"}
            reply = self._post(body, min(remaining, CALL_SECONDS), out)
            if reply is None:
                return out
            out.rounds += 1
            calls = reply.get("tool_calls") or []
            if not calls:
                out.text = reply.get("content") if isinstance(reply.get("content"), str) else None
                out.status = "ok"
                return out
            assistant: dict[str, Any] = {"role": "assistant", "content": reply.get("content") or "",
                                         "tool_calls": calls}
            if "reasoning_content" in reply:  # thinking left on: DeepSeek needs it passed back
                assistant["reasoning_content"] = reply["reasoning_content"]
            messages.append(assistant)
            for call in calls:
                result = self._run_tool(call, tools, run_calls < MAX_TOOL_CALLS, out)
                if result.get("error") != TOO_MANY:
                    run_calls += 1  # a refused call is not a call: the limit is on those run
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                 "content": as_json(result)})
        return out

    def _run_tool(self, call: dict, tools: Tools, allowed: bool, out: Outcome) -> dict:
        fn = call.get("function") or {}
        name = str(fn.get("name", ""))
        if not allowed:
            return {"error": TOO_MANY}
        try:
            arguments = json.loads(fn.get("arguments") or "{}")
        except (ValueError, TypeError):
            return {"error": BAD_ARGUMENTS}
        if not isinstance(arguments, dict):
            return {"error": BAD_ARGUMENTS}
        try:
            result = tools.call(name, arguments)
        except Exception:  # noqa: BLE001 - a tool that breaks ends no question
            log.exception("assistant tool %s failed", name)
            return {"error": TOOL_FAILED}
        out.calls.append(Trace(name, arguments, result))
        return result

    def _post(self, body: dict, timeout: float, out: Outcome) -> dict | None:
        """One model call: the reply message, or None with `out.status` saying why not."""
        try:
            r = self._client.post(self._url, json=body, timeout=timeout,
                                  headers={"Authorization": f"Bearer {self._key}"})
        except httpx.TimeoutException:
            out.status = "timeout"
            return None
        except httpx.HTTPError as e:
            log.warning("assistant request failed: %s", type(e).__name__)
            out.status = "error"
            return None
        if r.status_code >= 400:
            out.status = {402: "balance", 429: "rate_limited"}.get(r.status_code, "error")
            log.warning("assistant request refused: HTTP %s", r.status_code)
            return None
        try:
            message = r.json()["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError):
            log.warning("assistant reply was not a chat completion")
            out.status = "error"
            return None
        if not isinstance(message, dict):
            out.status = "error"
            return None
        return message
