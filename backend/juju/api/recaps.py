"""The owners' game recaps and history (/me): their saved lookups, each worked out again from
the archive and the stats every time it is read, by the same views the bet pages use. Nothing
is frozen, so a correction or the official stats check shows up here as it does on the page.

A recap lists prices across players, and history across games. That is allowed only as an
owner's own lookups, behind their own sign-in, never cached (docs/licensing.md). So a recap
card carries the price shown on its page and no other book's.
"""
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from juju.api.lookup import IN_PLAY
from juju.api.views import (
    CardView, Focus, LegView, ParlayView, PlayerView, TeamView, game_view, parlay_view,
    player_view, team_view,
)
from juju.core import card, parlay
from juju.core.card import STAKE, Outcome
from juju.core.enums import EventStatus, PlayKind
from juju.core.markets import BY_KEY
from juju.core.models import Game
from juju.tracker.models import SavedLookup

MOST_CARDS = 3  # per lookup: the bets it was about, as its page puts them first
SETTLED = frozenset({Outcome.WON, Outcome.LOST, Outcome.PUSH, Outcome.VOID})
PLAY_WORDS = {
    PlayKind.TOUCHDOWN: "a touchdown", PlayKind.PASS_TD: "a touchdown pass", PlayKind.RUN: "a run",
    PlayKind.CATCH: "a catch", PlayKind.PASS: "a pass", PlayKind.FIELD_GOAL: "a field goal",
    PlayKind.INTERCEPTION: "an interception thrown", PlayKind.DEF_INTERCEPTION: "an interception",
    PlayKind.SACK: "a sack",
}


def game_state(game: Game) -> str:
    if game.status is EventStatus.FINAL:
        return "checked" if game.verified_at is not None else "final"
    if game.status in IN_PLAY:
        return "in_play"
    return "pregame" if game.status is EventStatus.SCHEDULED else "off"


def _price(price: dict | None) -> dict | None:
    """The price as its page shows it: no other book, no snapshot detail."""
    if price is None:
        return None
    return {"american": price["american"], "book_name": price["book_name"],
            "minutes_before_kickoff": price["minutes_before_kickoff"],
            "timing": price["timing"]}


def _back(c: CardView) -> Decimal | None:
    """What $10 came back as, once the bet is settled; None while it isn't."""
    if c.outcome not in SETTLED or c.price is None:
        return None
    return card.settled_returns(c.outcome, card.money(c.price["american"], None))


def _bet(c: CardView) -> dict:
    return {"key": c.key, "label": c.label, "bet": c.bet, "line": c.line,
            "outcome": c.outcome, "outcome_text": c.outcome_text, "headline": c.headline,
            "current": c.current, "needed": c.needed, "price": _price(c.price),
            "no_price": c.no_price, "verified": c.verified, "decided_by": c.decided_by,
            "notes": c.notes, "back": _back(c)}


def _leg(leg: LegView) -> dict:
    return {"key": leg.key, "kind": leg.kind, "id": leg.id, "who": leg.who, "bet": leg.bet,
            "outcome": leg.outcome, "outcome_text": leg.outcome_text, "current": leg.current,
            "needed": leg.needed, "price": _price(leg.price), "no_price": leg.no_price}


def _parlay(v: ParlayView) -> dict:
    back = None
    if v.returns is not None and v.outcome in SETTLED:
        back = parlay.settled_returns(
            v.outcome, parlay.ParlayMoney(v.returns, v.fair_returns, v.legs_counted))
    return {"outcome": v.outcome, "outcome_text": v.outcome_text, "headline": v.headline,
            "book_name": v.book_name, "priced": v.returns is not None, "back": back,
            "legs": [_leg(leg) for leg in v.legs]}


def pick_cards(cards: Sequence[CardView]) -> tuple[list[CardView], int]:
    """The cards a lookup was about: the bets it named, else the ones its play touched, else
    the page's own first. Priced ones, in page order, at most MOST_CARDS; and how many of the
    named or touched ones have no price."""
    pool = ([c for c in cards if c.named] or [c for c in cards if c.touched])
    focused = bool(pool)
    pool = pool or list(cards)
    priced = [c for c in pool if c.price is not None][:MOST_CARDS]
    unpriced = sum(1 for c in pool if c.price is None) if focused else 0
    return priced, unpriced


def asked_text(saved: SavedLookup, the_play: dict | None) -> str | None:
    """What the page was opened for, in a few words; None for a plain look at someone."""
    if the_play is not None:
        return the_play["label"]
    market = BY_KEY.get(saved.market_key) if saved.market_key else None
    if market is not None:
        if saved.threshold is not None:  # an Over line: 79.5 is "80+"
            return f"{(saved.threshold + Decimal('0.5')).normalize():f}+ {market.label.lower()}"
        return market.label
    if saved.play:
        return PLAY_WORDS.get(PlayKind(saved.play))
    return None


@dataclass
class Totals:
    """Each bet counted once, however many lookups showed it. $10 on each settled bet."""
    bets: int = 0
    won: int = 0
    lost: int = 0
    stake_back: int = 0
    open: int = 0
    unpriced: int = 0
    staked: Decimal = Decimal("0.00")
    back: Decimal = Decimal("0.00")
    _seen: set = field(default_factory=set, repr=False)

    def add(self, key: tuple, outcome: Outcome, back: Decimal | None) -> None:
        if key in self._seen:
            return
        self._seen.add(key)
        self.bets += 1
        if back is None:
            self.open += 1
            return
        self.staked += STAKE
        self.back += back
        if outcome is Outcome.WON:
            self.won += 1
        elif outcome is Outcome.LOST:
            self.lost += 1
        else:
            self.stake_back += 1

    def as_dict(self) -> dict:
        return {"bets": self.bets, "won": self.won, "lost": self.lost,
                "stake_back": self.stake_back, "open": self.open, "unpriced": self.unpriced,
                "staked": self.staked, "back": self.back}


class Recaps:
    """Builds recap entries for one request, building each page's view once."""

    def __init__(self, session: Session, now: datetime, chain: Sequence[str]):
        self.session, self.now, self.chain = session, now, chain
        self._views: dict[tuple, PlayerView | TeamView | ParlayView | None] = {}

    def _view(self, game: Game, s: SavedLookup):
        key = (game.id, s.kind, s.target, s.play, s.market_key, s.threshold, s.yards, s.expect,
               s.play_id)
        if key not in self._views:
            focus = Focus(PlayKind(s.play) if s.play else None, s.market_key, s.threshold,
                          s.expect, s.yards)
            if s.kind == "player":
                view = player_view(self.session, game, s.target, self.now, self.chain, focus,
                                   s.play_id)
            elif s.kind == "team":
                view = team_view(self.session, game, s.target, self.now, self.chain,
                                 Focus(focus.play, focus.market_key), s.play_id)
            else:
                view = parlay_view(self.session, game, parlay.parse_legs(s.target), self.now,
                                   self.chain)
            self._views[key] = view
        return self._views[key]

    def entry(self, game: Game, s: SavedLookup, totals: Totals) -> dict:
        """One saved lookup as a recap shows it, counted into `totals`."""
        out = {"id": s.id, "kind": s.kind, "target": s.target, "play": s.play,
               "market": s.market_key, "threshold": s.threshold, "yards": s.yards,
               "expect": s.expect, "play_id": s.play_id, "times": s.times,
               "first_seen_at": s.first_seen_at, "last_seen_at": s.last_seen_at,
               "who": None, "asked": None, "bets": [], "parlay": None, "unpriced": 0,
               "no_price": None}
        view = self._view(game, s)
        if view is None:
            out["no_price"] = "This page isn't there any more."
            return out
        if isinstance(view, ParlayView):
            out["who"] = "Same-game parlay"
            out["parlay"] = _parlay(view)
            key = ("parlay", ",".join(sorted(s.target.split(","))))
            if view.returns is None:
                totals.unpriced += 1
            else:
                totals.add(key, view.outcome, out["parlay"]["back"])
            return out
        cards, unpriced = pick_cards(view.cards)
        if isinstance(view, PlayerView):
            out["who"] = view.player["name"]
            if not cards:
                out["no_price"] = view.no_price
        else:
            out["who"] = view.team["name"]
        out["asked"] = asked_text(s, view.the_play)
        out["bets"] = [_bet(c) for c in cards]
        out["unpriced"] = unpriced
        if not cards and out["no_price"] is None and view.cards:
            out["no_price"] = next((c.no_price for c in view.cards if c.no_price), None)
        totals.unpriced += unpriced
        for c, bet in zip(cards, out["bets"], strict=True):
            totals.add((s.kind, s.target, c.key, c.line), c.outcome, bet["back"])
        return out

    def recap(self, game: Game, lookups: Iterable[SavedLookup]) -> dict:
        totals = Totals()
        entries = [self.entry(game, s, totals) for s in lookups]
        return {"game": game_view(game, self.now), "state": game_state(game),
                "totals": totals.as_dict(), "lookups": entries}
