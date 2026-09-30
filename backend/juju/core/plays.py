"""From a play to the bets it touched (docs/GOALS.md section 7.2). Pure.

A user describes one play; a player has many archived markets. Cards are ordered so the
answer to "what did that play pay?" comes first:
1. a market the user named (or the threshold they gave, "100+ yards");
2. markets this kind of play touches that are already won or locked;
3. the other markets this kind of play touches;
4. everything else.
Within a group, main lines come before alternates, then catalog order.
"""
from dataclasses import dataclass

from juju.core.card import Outcome
from juju.core.enums import PlayKind
from juju.core.markets import CATALOG, Market

_CATALOG_ORDER = {m.key: i for i, m in enumerate(CATALOG)}
_CASHED = frozenset({Outcome.LOCKED, Outcome.WON})


@dataclass(frozen=True)
class Ranked:
    market: Market
    outcome: Outcome
    touched: bool
    named: bool


def rank(items: list[Ranked]) -> list[Ranked]:
    def key(r: Ranked) -> tuple:
        if r.named:
            group = 0
        elif r.touched and r.outcome in _CASHED:
            group = 1
        elif r.touched:
            group = 2
        else:
            group = 3
        return group, r.market.alternate, _CATALOG_ORDER.get(r.market.key, 999)
    return sorted(items, key=key)


def touches(market: Market, play: PlayKind | None) -> bool:
    return play is not None and play in market.touched_by
