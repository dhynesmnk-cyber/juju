"""The live chat's assistant, wired to the live data (docs/plans/live-chat.md, step 3).

`api/live_chat.py` hands a message here when it can't read a bet in it ("and the under?", "what
does +180 mean?"). A model (`ingest/assistant.py`) may then ask five tools for what it needs; the
tools are the only way it learns a number, and they run the same code as the plain answer:

- `find_player`, `get_player_stat`: free (the rosters, the cached replies, the box score);
- `list_markets`, `get_live_price`, `price_parlay`: live odds through the same 60-second cache,
  and through a ceiling of six credits for the whole question (`core/assistant.MAX_CREDITS`).

No tool returns a market or a board: a price is for one named player or team, a parlay for the 2
to 6 legs named, and a name that matches nobody is an error. What the model then says is checked
(`core/assistant.check_reply`): a number no tool gave, advice, a refusal, empty or over-long text
and the whole answer becomes the template one instead, built from the same tool results. The cards
shown with the words are the server's, from the legs the tools priced.
"""
import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from juju.api import live_chat as lc
from juju.api.views import BOOK_NAMES
from juju.config import Settings
from juju.core import assistant as rules
from juju.core import livechat
from juju.core.card import STAKE
from juju.core.markets import BY_KEY, CAPTURE_PRIORITY, Scope
from juju.core.models import LiveStat
from juju.ingest.assistant import Assistant
from juju.ingest.live_odds import Fetched, LiveError, LiveOdds, subjects
from juju.ingest.odds_api import ApiEvent
from juju.ingest.parse import LiveRead, Parsed
from juju.ingest.resolve import RosterEntry, normalize_name, rank_players

log = logging.getLogger("juju.live_assistant")

SIDES = {"over": "Over", "under": "Under", "yes": "Yes"}
LIMIT = ("This question has used its credit limit, so nothing more can be looked up. "
         "Answer with what you have.")


def _money(amount: Decimal) -> str:
    return format(amount.quantize(Decimal("0.01")), "f")


class Toolbox:
    """The tools of one question. It owns the question's credits: every call to The Odds API
    goes through `_fetch`, which counts what it cost and stops at the ceiling."""

    def __init__(self, session: Session, event: ApiEvent, live: LiveOdds, settings: Settings):
        self.session, self.event, self.live, self.settings = session, event, live, settings
        self.used: list[Fetched] = []            # the odds replies it used (when they are from)
        self.spent = 0                           # credits this question has cost
        self.legs: list[lc.Leg] = []             # the bets it priced, one for each distinct bet
        self.misses: list[lc.Leg] = []           # bets it asked for that no book posts
        self.parlay_legs: list[lc.Leg] | None = None
        self.parlay: livechat.ParlayPrice | None = None

    # --- credits ------------------------------------------------------------------------------

    def _held(self, free: bool) -> None:
        if not free and self.spent >= rules.MAX_CREDITS:
            raise LiveError(LIMIT, "credit_limit")

    def _fetch(self, key: str) -> Fetched:
        self._held(self.live.cached(self.event.id, key))
        fetched = self.live.odds(self.event.id, key)
        self.used.append(fetched)
        self.spent += fetched.cost
        return fetched

    # --- the door the model knocks on ---------------------------------------------------------

    def call(self, name: str, arguments: dict[str, Any]) -> dict:
        try:
            match name:
                case "find_player":
                    return self._find_player(arguments)
                case "list_markets":
                    return self._list_markets()
                case "get_live_price":
                    return self._get_live_price(arguments)
                case "get_player_stat":
                    return self._player_stat(arguments)
                case "price_parlay":
                    return self._price_parlay(arguments)
                case _:
                    return {"error": f"There is no tool called {name[:40]}."}
        except LiveError as e:
            return {"error": e.message}

    # --- free tools ---------------------------------------------------------------------------

    def _find_player(self, args: dict) -> dict:
        name = str(args.get("name", "")).strip()[:60]
        if not name:
            return {"error": "Give a name."}
        people: dict[str, str | None] = {}
        _, players = lc._roster(self.session, self.event.id)
        for p in players:
            people[p.name] = p.position
        for key, market in BY_KEY.items():     # names from replies the last minute already fetched
            if market.scope is Scope.PLAYER and not market.alternate:
                if (hit := self.live.peek(self.event.id, key)) is not None:
                    for n in subjects(hit.value, key):
                        people.setdefault(n, None)
        ranked = rank_players(name, [RosterEntry(n, n) for n in people], limit=5)
        return {"matches": [{"name": c.entry.name, "position": people[c.entry.name]}
                            for c in ranked]}

    def _player_stat(self, args: dict) -> dict:
        name = str(args.get("player", "")).strip()[:60]
        game, players = lc._roster(self.session, self.event.id)
        if game is None:
            return {"message": "Juju isn't following this game's box score."}
        player = lc._player_in_db(name, players)
        if player is None:
            return {"message": "No player matches that name on this game's rosters."}
        rows = list(self.session.scalars(select(LiveStat).where(
            LiveStat.game_id == game.id, LiveStat.espn_athlete_id == player.espn_athlete_id)))
        if not rows:
            return {"player": player.name, "message": "He has no stat line yet."}
        return {"player": player.name,
                "stats": [{"stat": r.stat.value.replace("_", " "),
                           "value": format(r.value.normalize(), "f")}
                          for r in sorted(rows, key=lambda r: r.stat.value)]}

    # --- tools that look at live odds ---------------------------------------------------------

    def _list_markets(self) -> dict:
        self._held(self.live.markets_cached(self.event.id))
        fetched = self.live.markets(self.event.id)
        self.spent += fetched.cost
        order = {k: i for i, k in enumerate(CAPTURE_PRIORITY)}
        labels = [BY_KEY[k].label for k in sorted((k for k in fetched.value if k in BY_KEY),
                                                  key=lambda k: order[k])]
        return {"markets": list(dict.fromkeys(labels))}

    def _read(self, spec: object) -> LiveRead | str:
        """One bet as the model named it, as the parser would have read it; or what's wrong."""
        if not isinstance(spec, dict):
            return "A bet is an object with a subject and a market."
        key = spec.get("market")
        if key not in rules.main_markets():
            return "That is not a market the tools know."
        market = BY_KEY[str(key)]
        subject = str(spec.get("subject") or "").strip()[:60]
        if market.scope is not Scope.TOTAL and not subject:
            return "Name a player or a team: one at a time."
        side = SIDES.get(str(spec.get("side") or "over").lower())
        if side is None:
            return "The side is over, under or yes."
        line: Decimal | None = None
        if spec.get("line") is not None:
            try:
                line = Decimal(str(spec["line"]))
            except InvalidOperation:
                return "The line must be a number."
            if abs(line) > 1000:
                return "That line is out of range."
        norm = normalize_name(subject)
        spread = market.scope is Scope.SPREAD
        parsed = Parsed(norm, None, None, None, str(key), norm)
        return LiveRead(parsed, norm, side, None if spread else line, line if spread else None,
                        False)

    def _price(self, specs: list[object]) -> list[lc.Leg] | str:
        """Plan the bets, fetch what they need (counted), answer them. An error message, or the
        legs: each answered, or with `missing` saying why it has no price."""
        legs: list[lc.Leg] = []
        for spec in specs:
            read = self._read(spec)
            if isinstance(read, str):
                return read
            planned = lc._plan_leg(self.session, self.event, read)
            if isinstance(planned, str):
                return planned.replace(lc.EXAMPLES, "").strip()   # the examples carry numbers
            legs.append(planned)
        lc._fetch_and_answer(legs, self.event, self.session, self.live, self.settings,
                             fetch=self._fetch)
        return legs

    def _leg_facts(self, leg: lc.Leg) -> dict:
        a = leg.answer
        assert a is not None
        other = livechat.opposite_side(lc._side(leg) or "")
        lines = []
        for pick in a.picks:
            lines.append({
                "bet": lc._bet(leg, pick.point),
                "line": None if pick.point is None else format(pick.point.normalize(), "f"),
                "book": BOOK_NAMES.get(pick.book.book, pick.book.book),
                "american": pick.book.american,
                "returns_on_10_dollars": _money(pick.book.returns),
                "profit_on_10_dollars": _money(pick.book.returns - STAKE),
                "opposite_side": other if pick.opposite else None,
                "opposite_american": pick.opposite.american if pick.opposite else None,
                "other_books": [{"book": BOOK_NAMES.get(p.book, p.book), "american": p.american}
                                for p in pick.others]})
        facts: dict[str, Any] = {
            "available": True, "market": leg.market.label, "subject": leg.subject or leg.team,
            "line_asked": None if a.asked is None else format(a.asked.normalize(), "f"),
            "asked_line_is_posted": a.exact, "lines": lines}
        if leg.stands:
            facts["box_score"] = leg.stands
        return facts

    @staticmethod
    def _ident(leg: lc.Leg) -> tuple:
        """Which bet a leg is: the same bet asked twice is one card."""
        a = leg.answer
        return (leg.market.key, leg.subject or leg.team, lc._side(leg),
                None if a is None else a.asked)

    def _remember(self, leg: lc.Leg) -> None:
        if all(self._ident(x) != self._ident(leg) for x in self.legs):
            self.legs.append(leg)

    def _get_live_price(self, args: dict) -> dict:
        legs = self._price([args])
        if isinstance(legs, str):
            return {"error": legs}
        leg = legs[0]
        if leg.answer is None or not leg.answer.picks:
            self.misses.append(leg)
            return {"available": False,
                    "message": leg.missing or "Market not available: no book posts that bet "
                                              "on that side right now."}
        self._remember(leg)
        return self._leg_facts(leg)

    def _price_parlay(self, args: dict) -> dict:
        specs = args.get("legs")
        if not isinstance(specs, list) or not 2 <= len(specs) <= livechat.MAX_LEGS:
            return {"error": "A parlay has two to six legs."}
        legs = self._price(specs)
        if isinstance(legs, str):
            return {"error": legs}
        if len({(x.market.key, x.subject or x.team, lc._side(x), lc._asked_line(x))
                for x in legs}) != len(legs):
            return {"error": "The same leg twice."}
        lacking = [x.missing for x in legs if x.answer is None]
        if lacking:
            return {"error": "Can't price it. " + " ".join(m or "" for m in lacking)}
        if any(not x.answer.picks or not x.answer.exact for x in legs if x.answer):
            return {"error": "Every leg needs a posted line, and at least one has none.",
                    "legs": [self._leg_facts(x) for x in legs if x.answer and x.answer.picks]}
        picks = [x.answer.picks[0] for x in legs if x.answer]
        priced = livechat.price_parlay(picks, self.settings.books)
        if priced is None:
            return {"error": "That parlay can't be priced."}
        self.parlay_legs, self.parlay = legs, priced
        return {"legs": [self._leg_facts(x) for x in legs],
                "parlay": {"book": BOOK_NAMES.get(priced.book, priced.book) if priced.book
                           else "each leg at its own book",
                           "returns_on_10_dollars": _money(priced.returns),
                           "profit_on_10_dollars": _money(priced.returns - STAKE),
                           "notes": list(priced.notes)}}


def reply(body: lc.AskIn, event: ApiEvent, session: Session, live: LiveOdds, settings: Settings,
          assistant: Assistant, *, fallback: str) -> dict:
    """Answer a message the plain pipeline couldn't read. The model's words are used only if they
    pass the checks; otherwise the template answer for whatever the tools priced, or `fallback`
    (the plain pipeline's own question) when they priced nothing."""
    tools = Toolbox(session, event, live, settings)
    history = [(t.role, t.text) for t in body.history]
    outcome = assistant.run(rules.system_prompt(f"{event.away_team} @ {event.home_team}"),
                            history, body.text, tools)
    facts = rules.facts_from([t.result for t in outcome.calls], body.text)
    if outcome.status == "ok":
        verdict = rules.check_reply(outcome.text, facts)
    else:
        verdict = rules.Verdict(False, reason=outcome.status)
    # What went wrong, never the words: "advice", "number", "empty", "timeout", "budget", ...
    log.info("assistant: %s after %d tool calls", "ok" if verdict.ok else
             (verdict.reason or "?").split()[0], len(outcome.calls))

    legs = tools.parlay_legs or tools.legs
    answered = [leg for leg in legs if leg.answer is not None and leg.answer.picks]
    credits = {"credits": lc.credits_block(live, settings), "disclaimer": lc.DISCLAIMER}
    meta = lc._meta(tools.used) | {"cost": tools.spent}
    if verdict.ok:
        return {"kind": "chat", "message": verdict.text, "sentences": [verdict.text],
                "legs": [lc._leg_view(leg) for leg in answered],
                "parlay": lc._parlay_view(tools.parlay) if tools.parlay else None,
                "choices": [], "ai": True, **meta, **credits}
    if verdict.reason == "refused":
        return {**lc._reply(live, settings, "refused", rules.REFUSAL_TEXT), "cost": tools.spent}
    if answered or tools.misses:
        template = lc._compose(answered or tools.misses, tools.used, live, settings,
                               as_parlay=tools.parlay is not None)
        return {**template, "cost": tools.spent}
    return {**lc._reply(live, settings, "ask", fallback), "cost": tools.spent}
