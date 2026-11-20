"""What the live chat's assistant may say (docs/plans/live-chat.md, step 3). Pure: no I/O.

The assistant is a model that can ask the server for live prices and stats, and word the answer.
It never supplies a displayed number, and it never gives a pick. This module is the part that
holds it to that, whatever the model does:

- **Numbers.** Every number in its text must be in a tool result from this very question, or in
  the person's own message. A price (`+170`) must match a price the tools gave, sign included.
  Numbers from earlier turns do not count: those come from the browser, which can write
  anything. Small plain counts (up to twelve) and the $10 stake are free: "2 legs", "$10".
- **No picks.** Recommending, predicting, "good bet" and the like are refused, in what the
  model says and in what a person asks.
- **Scope.** The model is told to answer `[[REFUSE]]` for anything but this game's bets and
  stats; the server replaces it with fixed words.

When any check fails, the caller uses the template sentences instead (`core/livechat.py`), so a
bad reply never reaches the page.
"""
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from juju.core.markets import BY_KEY

REFUSE_TOKEN = "[[REFUSE]]"
REFUSAL_TEXT = ("I can only help with this game's player and team bets, and where its players "
                "stand. I don't give picks or tell you what to bet. Try asking for a price, "
                "like \"Fannin got 4 catches\".")
ADVICE_TEXT = "Juju doesn't give picks or say what to bet, so this is the price and nothing more."
MAX_REPLY_CHARS = 700
FREE_COUNT = Decimal(12)   # a bare whole number up to this is a count, not a price
STAKE = Decimal(10)

# The most a question may ask of the tools (docs/plans/live-chat.md): calls, and Odds API credits.
MAX_TOOL_CALLS = 4
MAX_CREDITS = 6


# --- The words the model is given ---------------------------------------------------------------


def main_markets() -> list[str]:
    """The market keys the model may ask for: the catalog's main markets. (Alternate ladders are
    looked at by the server when an exact line isn't in the main one.)"""
    return [k for k, m in BY_KEY.items() if not m.alternate]


def system_prompt(game: str) -> str:
    labels = "; ".join(f"{k} = {BY_KEY[k].label}" for k in main_markets())
    return (
        f"You are Juju's live odds assistant for one NFL game: {game}. You help someone ask what "
        "a sportsbook posts right now for a player or a team in this game, and where players "
        "stand in it.\n\n"
        "Rules. All of them are strict.\n"
        "1. Every price, line, payout and stat you state must come from a tool result in this "
        "conversation. Call a tool for it. Never recall, estimate or work out a number yourself.\n"
        "2. Say prices in the present tense, for example \"pays +170 at DraftKings right now: $10 "
        "would return $27.00 if it hits\". The stake is always $10. Name the book the tool names.\n"
        "3. Never recommend a bet, pick a side, say what will happen, or say whether a price is "
        "good. If you are asked for that, or the question is not about this game's bets or its "
        f"players' stats, reply with exactly {REFUSE_TOKEN} and nothing else.\n"
        "4. One player or one team at a time. You cannot list a market's players or prices, so do "
        "not try. Use find_player when a name is unclear.\n"
        "5. If it is unclear which player, team, bet or line is meant, ask one short question "
        "instead of guessing. Use the earlier messages to resolve \"him\", \"the under\" or "
        "\"that line\".\n"
        "6. If a tool says a market is not available, say so plainly, starting \"Market not "
        "available\".\n"
        "7. Answer in at most three short sentences of plain text: no markdown, no lists, no "
        "links, and no numbers except those from tools.\n\n"
        f"Markets for the market field of the tools: {labels}.")


def tool_specs() -> list[dict]:
    """The tools, in the OpenAI function format DeepSeek takes (`tools`)."""
    subject = {"type": "string", "description": "A player's name, or a team's name, as typed."}
    market = {"type": "string", "enum": main_markets(),
              "description": "The market key. Receptions are player_receptions, receiving yards "
                             "player_reception_yds, a team's spread is spreads."}
    side = {"type": "string", "enum": ["over", "under", "yes"],
            "description": "Over or under for a stat line; yes for an anytime touchdown. "
                           "Default over."}
    line = {"type": "number",
            "description": "The line asked for, such as 3.5. \"4 catches\" is 3.5 (four or more). "
                           "A spread's handicap, such as -3.5. Leave it out to ask for the "
                           "posted line."}
    leg = {"type": "object", "properties": {"subject": subject, "market": market, "side": side,
                                            "line": line},
           "required": ["subject", "market"]}
    return [
        {"type": "function", "function": {
            "name": "find_player",
            "description": "Find who a typed name means in this game. Free. Returns up to five "
                           "names, with positions when known, and no prices.",
            "parameters": {"type": "object", "properties": {"name": {"type": "string"}},
                           "required": ["name"]}}},
        {"type": "function", "function": {
            "name": "list_markets",
            "description": "Which bets the books post for this game, by name. Costs a credit "
                           "unless already looked up in the last five minutes.",
            "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {
            "name": "get_live_price",
            "description": "The live price of one bet on one player or team: the book of "
                           "record's price, what $10 returns, the other side and the other "
                           "books. Costs a credit unless the market was looked up in the last "
                           "minute.",
            "parameters": {"type": "object", "properties": {
                "subject": subject, "market": market, "side": side, "line": line},
                "required": ["subject", "market"]}}},
        {"type": "function", "function": {
            "name": "get_player_stat",
            "description": "Where a player stands in this game now, from the box score. Free.",
            "parameters": {"type": "object", "properties": {"player": subject},
                           "required": ["player"]}}},
        {"type": "function", "function": {
            "name": "price_parlay",
            "description": "The live price of a same-game parlay of 2 to 6 legs, at one book "
                           "when one posts every leg. Costs a credit for each market not looked "
                           "up in the last minute.",
            "parameters": {"type": "object", "properties": {
                "legs": {"type": "array", "items": leg, "minItems": 2, "maxItems": 6}},
                "required": ["legs"]}}},
    ]


# --- Numbers ------------------------------------------------------------------------------------

# A number in text, with the sign and "$" that go with it. "4th" and "49ers" are not numbers; a
# sign counts only when nothing word-like comes before it ("2-leg" has no sign).
_NUMBER = re.compile(r"(?<![\w.])([+\-−])?(\$?)(\d[\d,]*(?:\.\d+)?)(?!\w)")


@dataclass(frozen=True)
class Facts:
    """The numbers a reply may use: exactly (with their signs) and as magnitudes."""
    signed: frozenset[Decimal]
    magnitudes: frozenset[Decimal]


def _value(digits: str) -> Decimal | None:
    try:
        return Decimal(digits.replace(",", ""))
    except InvalidOperation:
        return None


def numbers_in(text: str) -> list[tuple[str, Decimal, bool, bool]]:
    """(the token, its signed value, whether it had a sign, whether it had a decimal point)."""
    out = []
    for m in _NUMBER.finditer(text):
        value = _value(m.group(3))
        if value is None:
            continue
        sign = m.group(1)
        if sign in ("-", "−"):
            value = -value
        out.append((m.group(0), value, sign is not None, "." in m.group(3)))
    return out


def _walk(obj: object) -> Iterable[Decimal]:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        yield Decimal(str(obj))
    elif isinstance(obj, str):
        for _, value, _, _ in numbers_in(obj):
            yield value
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk(v)


def facts_from(results: Sequence[object], message: str = "") -> Facts:
    """The numbers of this question's tool results (data, as the tools returned them), and of the
    person's own message. Nothing else: not earlier turns."""
    values = {v for r in results for v in _walk(r)}
    values |= {v for _, v, _, _ in numbers_in(message)}
    return Facts(frozenset(values), frozenset(abs(v) for v in values))


def stray_numbers(text: str, facts: Facts) -> list[str]:
    """The numbers in `text` that no tool result and no word of the person's backs."""
    stray = []
    for token, value, has_sign, has_point in numbers_in(text):
        if abs(value) == STAKE:
            continue                                   # the $10 stake
        if (not has_sign and not has_point and not token.startswith("$")
                and 0 <= value <= FREE_COUNT):
            continue                                   # a count: "2 legs", "three books"
        ok = value in facts.signed if has_sign else abs(value) in facts.magnitudes
        if not ok:
            stray.append(token)
    return stray


# --- Picks --------------------------------------------------------------------------------------

_ADVICE = [re.compile(p, re.IGNORECASE) for p in (
    r"\byou (should|could|might want to|may want to|ought to|need to) (bet|take|play|back|hit|go|"
    r"pick|consider|grab|fade|tail)\b",
    r"\b(i|we) (would |'d |will )?(recommend|suggest|advise|like|love|take|bet|back|play)\b(?! "
    r"(that|this|the price))",
    r"\b(best|good|great|solid|safe|smart|strong|sharp|easy|free|lock|juicy) (bet|play|pick|"
    r"value|spot|money|parlay)\b",
    r"\bvalue (bet|play)\b", r"\bworth (a |the )?(bet|shot|play|taking|backing|it)\b",
    r"\b(sure thing|can't lose|cannot lose|lock of the)\b",
    r"\b(take|hammer|smash|fade|tail|back) the (over|under)\b",
    r"\b(will|'ll|is going to|are going to|should|likely to|expected to|probably will|"
    r"bound to) (hit|win|cover|go over|go under|score|get there|cash|beat)\b",
    r"\b(good|bad|great|poor|fair|generous|stingy) (price|odds|number|line)\b",
    r"https?://|www\.",
)]

_ASKS_FOR_A_PICK = [re.compile(p, re.IGNORECASE) for p in (
    r"\bshould (i|we)\b", r"\b(do|would|can) you (recommend|suggest|pick|advise)\b",
    r"\bwhat (should|do you think|would you)\b",
    r"\b(best|good|safe|smart|worth(while)?|sharp|lock|value) (bet|play|pick|parlay|prop|spot)\b",
    r"\bis (it|that|this|he|she) (a )?(good|safe|smart|worth|likely|going to|gonna)\b",
    r"\bwho (will|should|wins|is going to|'s going to)\b",
    r"\b(give|build|make|suggest|pick|find) (me )?(a |some |the )?(best |good )?(parlay|bets?|"
    r"picks?|prop)\b",
    r"\b(your|any) (picks?|predictions?|advice|recommendations?)\b", r"\bpredict",
)]


def advice_seeking(message: str) -> bool:
    """Is the person asking Juju to pick, predict or recommend? Juju gives facts, never picks."""
    return any(p.search(message) for p in _ASKS_FOR_A_PICK)


def gives_advice(text: str) -> bool:
    return any(p.search(text) for p in _ADVICE)


# --- The check ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    ok: bool
    text: str = ""            # the reply, cleaned, when ok
    reason: str | None = None  # why not: "empty", "refused", "too long", "advice", "number 3.5"


_MARKDOWN = re.compile(r"[*_`#>]+")


def clean(text: str) -> str:
    """Plain text only: the page draws what it is given, and a model may add markdown."""
    return re.sub(r"[ \t]+", " ", _MARKDOWN.sub("", text)).strip()


def check_reply(text: str | None, facts: Facts) -> Verdict:
    """May this go to the page as the assistant's words? Refusals, empty and too-long text,
    advice and any number the facts don't back all say no."""
    if not text or not text.strip():
        return Verdict(False, reason="empty")
    if REFUSE_TOKEN in text:
        return Verdict(False, reason="refused")
    cleaned = clean(text)
    if len(cleaned) > MAX_REPLY_CHARS:
        return Verdict(False, reason="too long")
    if gives_advice(cleaned):
        return Verdict(False, reason="advice")
    if stray := stray_numbers(cleaned, facts):
        return Verdict(False, reason=f"number {stray[0]}")
    return Verdict(True, cleaned)


def as_json(result: object) -> str:
    """A tool result as the model is shown it."""
    return json.dumps(result, default=str, separators=(",", ":"))
