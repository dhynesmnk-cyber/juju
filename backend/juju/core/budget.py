"""How many Odds API credits a game's captures may spend (docs/GOALS.md section 4). Pure.

The owner chose on 2026-10-15: two captures a game, the main markets at T-3h and every market at
T-45. A capture costs one credit per market that comes back (up to 10 books are one region), so
a game costs at most FULL_COST. The credits left are shared out over the games left before the
plan resets, and each game gets the best plan that fits:

- FULL: both captures;
- T45_ONLY: every market at T-45, no T-3h check (so no early price to fall back on);
- LEAN: T-45 only, with the bets people ask about most (`CAPTURE_PRIORITY`);
- NONE: nothing, and the game's cards say why.

An unknown quota (before the vendor's first response) is FULL: the first reply says how much is
left. The T-45 capture may still spend below the reserve (`worker/capture.py`); this only makes
sure the credits last the month.
"""
import enum
import math
from dataclasses import dataclass
from datetime import datetime

from juju.core.markets import ALL_KEYS, CAPTURE_PRIORITY, MAIN_KEYS


class Tier(enum.StrEnum):
    FULL = "full"
    T45_ONLY = "t45_only"
    LEAN = "lean"
    NONE = "none"


FULL_COST = len(MAIN_KEYS) + len(ALL_KEYS)  # an upper bound: unlisted markets cost nothing
T45_COST = len(ALL_KEYS)
LEAN_MIN = 3  # moneyline, spread and game total, at least

NO_CREDITS_NOTE = "Juju had used its odds credits for the month when this game started."


@dataclass(frozen=True)
class Plan:
    tier: Tier
    markets: tuple[str, ...]  # what the T-45 capture asks for
    per_game: int | None      # credits each game left may spend; None when the quota is unknown

    @property
    def early(self) -> bool:
        """Whether the T-3h capture runs."""
        return self.tier is Tier.FULL

    @property
    def note(self) -> str | None:
        """What a card with no price says about this plan, if anything."""
        return NO_CREDITS_NOTE if self.tier is Tier.NONE else None


def next_reset(now: datetime, day: int) -> datetime:
    """When the credits next reset: `day` (1 to 28) of the month, at 00:00 UTC."""
    this_month = now.replace(day=day, hour=0, minute=0, second=0, microsecond=0)
    if this_month > now:
        return this_month
    if now.month == 12:
        return this_month.replace(year=now.year + 1, month=1)
    return this_month.replace(month=now.month + 1)


def games_left(known: int, now: datetime, reset_at: datetime, per_week: int) -> int:
    """The games still to capture before the reset: the ones already scheduled, or a full
    week's worth for each week left if that is more (the vendor lists only the next few days)."""
    days = max((reset_at - now).total_seconds() / 86_400, 0.0)
    return max(known, math.ceil(days * per_week / 7), 1)


def plan(quota: int | None, games: int) -> Plan:
    """The best capture plan that leaves enough for every game left."""
    if quota is None:
        return Plan(Tier.FULL, ALL_KEYS, None)
    per_game = max(quota, 0) // max(games, 1)
    if per_game >= FULL_COST:
        return Plan(Tier.FULL, ALL_KEYS, per_game)
    if per_game >= T45_COST:
        return Plan(Tier.T45_ONLY, ALL_KEYS, per_game)
    if per_game >= LEAN_MIN:
        return Plan(Tier.LEAN, CAPTURE_PRIORITY[:per_game], per_game)
    return Plan(Tier.NONE, (), per_game)
