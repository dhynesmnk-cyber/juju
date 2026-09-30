"""Win, loss or push from a final value (after parlaytracker@c3bd43c core/settlement.py).

Pure functions: when something settles is decided by the card rules (`core/card.py`).
"""
from decimal import Decimal

from juju.core.enums import LegResult


def settle_over(line: Decimal, value: Decimal | int) -> LegResult:
    """An Over (or a "Yes", which is an Over 0.5): win above the line, push on it."""
    x = value - line
    return LegResult.WIN if x > 0 else LegResult.PUSH if x == 0 else LegResult.LOSS


def settle_spread(line: Decimal, margin: Decimal | int) -> LegResult:
    """A side at `line` (e.g. -3.5), given its final margin: it must win by more than -line."""
    x = margin + line
    return LegResult.WIN if x > 0 else LegResult.PUSH if x == 0 else LegResult.LOSS


def settle_moneyline(margin: Decimal | int) -> LegResult:
    """A side to win outright. A tie (possible in the NFL) is a push at most books."""
    return LegResult.WIN if margin > 0 else LegResult.PUSH if margin == 0 else LegResult.LOSS
