"""The LLM fallback for free text (docs/GOALS.md section 7.4).

After parlaytracker@c3bd43c parlaytracker/ingest/extraction.py: one call, `temperature=0`, a
JSON schema, and a forgiving `parse_reply` (code fences, string numbers; a bad field is dropped
and the rest kept). It is called only when the deterministic parse isn't sure. It has a short
timeout and a daily budget, and it is off unless a key and a model are configured.

What it returns is a *reading* of the text: a player's name, a team, a kind of play. That
reading goes through the same roster matching as typed text. It never supplies a number that
is shown to anyone.
"""
import json
import logging
import re
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import httpx

from juju.core.enums import PlayKind

log = logging.getLogger("juju.llm")

TIMEOUT_SECONDS = 3.0

PROMPT = """You read one line a sports fan typed about an NFL play that just happened. \
Return JSON matching the schema. Use null for anything the text doesn't say. Do not guess \
names: copy the player's name as written (fix obvious typos only). play is one of: \
touchdown (the player scored), pass_td (the player threw a touchdown), run, catch, pass, \
field_goal, interception, def_interception, sack, unknown.

Text: """

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "player_name": {"type": ["string", "null"]},
        "team": {"type": ["string", "null"]},
        "play": {"type": ["string", "null"], "enum": [k.value for k in PlayKind] + [None]},
        "yards": {"type": ["integer", "null"]},
    },
    "required": ["player_name", "team", "play", "yards"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Reading:
    player_name: str | None
    team: str | None
    play: PlayKind | None
    yards: int | None


_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


def parse_reply(reply: str) -> Reading | None:
    """Forgiving: fences stripped, string numbers read, a bad field dropped."""
    try:
        data = json.loads(_FENCE.sub("", reply.strip()))
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    def text(key: str) -> str | None:
        v = data.get(key)
        return v.strip()[:80] if isinstance(v, str) and v.strip() else None

    play = None
    try:
        play = PlayKind(data.get("play")) if data.get("play") else None
    except ValueError:
        pass
    yards = data.get("yards")
    try:
        yards = int(float(str(yards).replace("−", "-"))) if yards is not None else None
    except ValueError:
        yards = None
    return Reading(text("player_name"), text("team"), play, yards)


class Budget:
    """Calls per UTC day across this process. Past the budget, the fallback is off until
    midnight, and free text still gets the deterministic "Did you mean…" candidates."""

    def __init__(self, per_day: int):
        self.per_day = per_day
        self._day: date | None = None
        self._used = 0
        self._lock = threading.Lock()

    def take(self, now: datetime | None = None) -> bool:
        today = (now or datetime.now(tz=UTC)).date()
        with self._lock:
            if today != self._day:
                self._day, self._used = today, 0
            if self._used >= self.per_day:
                return False
            self._used += 1
            return True


class LlmReader:
    def __init__(self, api_key: str, base_url: str, model: str, budget: Budget,
                 client: httpx.Client | None = None):
        self._key = api_key
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._budget = budget
        self._client = client or httpx.Client(timeout=TIMEOUT_SECONDS)

    def read(self, text: str) -> Reading | None:
        """One call; None on any failure (the caller falls back to candidates)."""
        if not self._budget.take():
            log.warning("LLM budget used up for today")
            return None
        body = {
            "model": self._model, "temperature": 0,
            "messages": [{"role": "user", "content": PROMPT + text}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "PlayReading", "strict": True,
                                                "schema": SCHEMA}},
        }
        try:
            r = self._client.post(self._url, json=body, timeout=TIMEOUT_SECONDS,
                                  headers={"Authorization": f"Bearer {self._key}"})
            r.raise_for_status()
            reply = r.json()["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001 - any failure means "no reading"; never the key
            log.warning("LLM fallback failed: %s", type(e).__name__)
            return None
        return parse_reply(reply) if isinstance(reply, str) else None
