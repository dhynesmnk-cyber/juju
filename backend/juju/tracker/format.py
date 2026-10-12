# Ported from parlaytracker@c3bd43c parlaytracker/app/common.py (its formatting), app/pages/live.py
# (fmt_value, fmt_age and the words a leg's standing is told in) and app/pages/analytics.py (the
# texts of its figures). Changes: no Streamlit; the display time zone is an argument; and a leg's
# standing comes back as its own text instead of inside a Markdown line, so the site lays it out.
"""How the tracker says things: the words and numbers its pages show, built server-side so the
site never formats betting maths itself (docs/plans/integrate-parlaytracker.md section 6.5)."""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from juju.tracker.analytics import Mean, SelectionStats
from juju.tracker.live import LegView, State
from juju.tracker.models import MarketType, Slip, TeamSide

DASH = "—"

MARKET_LABELS = {
    MarketType.GAME_TOTAL: "Game total",
    MarketType.TEAM_TOTAL: "Team total",
    MarketType.ALT_SPREAD: "Alt spread",
    MarketType.PLAYER_RECEPTIONS: "Receptions",
    MarketType.PLAYER_RECEIVING_YARDS: "Receiving yards",
    MarketType.PLAYER_RUSHING_YARDS: "Rushing yards",
    MarketType.PLAYER_PASSING_YARDS: "Passing yards",
    MarketType.PLAYER_PASS_COMPLETIONS: "Pass completions",
    MarketType.PLAYER_TOUCHDOWNS: "Touchdowns",
    MarketType.PLAYER_INTERCEPTIONS: "Interceptions thrown",
    MarketType.PLAYER_FIELD_GOALS: "Field goals made",
    MarketType.PLAYER_POINTS: "Points",
    MarketType.OTHER: "Other (settled by hand)",
}
SLIP_KINDS = {"single": "Single", "parlay": "Parlay", "sgp": "Same-game parlay"}


def fmt_time(moment: datetime | None, tz: ZoneInfo) -> str:
    if moment is None:
        return DASH
    return moment.astimezone(tz).strftime("%a %d %b %H:%M")


def fmt_odds(odds: int | None) -> str:
    return DASH if odds is None else f"{odds:+d}"


def fmt_money(amount: Decimal | None, placed: bool = True) -> str:
    if amount is None:
        return DASH
    return f"${amount:,.2f}" if placed else f"{amount:.2f}u"


def _num(value: Decimal) -> str:
    return f"{value:.1f}".removesuffix(".0")


def fmt_line(line: Decimal | None) -> str:
    """A spread as shown on a slip: +3.5, -7."""
    if line is None:
        return ""
    return f"+{_num(line)}" if line > 0 else _num(line)


def _side_team(leg) -> str:
    return leg.event.home_team if leg.side is TeamSide.HOME else leg.event.away_team


def game_label(leg) -> str:
    return f"{leg.event.away_team} @ {leg.event.home_team}"


def pick_text(leg) -> str:
    """What was picked, without the game or the odds: 'Rushing yards · Saquon Barkley o84.5'."""
    market = MARKET_LABELS[leg.market_type]
    if leg.market_type is MarketType.OTHER:
        return leg.description
    if leg.market_type is MarketType.ALT_SPREAD:
        return f"{market} · {_side_team(leg)} {fmt_line(leg.line)}"
    if leg.market_type is MarketType.TEAM_TOTAL:
        return f"{market} · {_side_team(leg)} o{_num(leg.line)}"
    if leg.market_type is MarketType.GAME_TOTAL:
        return f"{market} o{_num(leg.line)}"
    return f"{market} · {leg.player_name or 'player ' + leg.espn_athlete_id} o{_num(leg.line)}"


def leg_summary(leg) -> str:
    """One line describing a leg, e.g. 'PHI @ CHI · Rushing yards · Saquon Barkley o84.5 -115'."""
    odds = fmt_odds(leg.american_odds) if leg.american_odds is not None else ""
    return f"{game_label(leg)} · {pick_text(leg)} {odds}".rstrip()


def slip_summary(slip: Slip) -> str:
    kind = SLIP_KINDS[slip.slip_type.value]
    money = f"{fmt_money(slip.stake)} to win" if slip.is_placed else "Not placed"
    return (f"{kind} {fmt_odds(slip.american_odds)} · {slip.sportsbook.name} · {money} · "
            f"logged by {slip.logged_by}")


# --- The Watch page (parlaytracker app/pages/live.py) --------------------------------------------

NOT_TRACKED = "Not tracked live: settles after the game"
NOTHING_TO_WATCH = ("Nothing to watch right now. A card appears for a pending slip from half an "
                    "hour before an NFL game kicks off until it finishes.")
STATE_WORDS = {State.OVER: "over", State.UNDER: "under", State.LEVEL: "level",
               State.UNKNOWN: "no value yet"}
SPREAD_WORDS = {State.OVER: "covering", State.UNDER: "not covering"}


def fmt_value(value: Decimal | None) -> str:
    return DASH if value is None else f"{value:.1f}".removesuffix(".0")


def fmt_age(seconds: int | None) -> str:
    if seconds is None:
        return "never"
    return f"{seconds} s ago" if seconds < 120 else f"{seconds // 60} min ago"


def standing(view: LegView) -> str:
    """Where one leg stands: its result once settled, else its live value and over or under."""
    leg = view.leg
    if view.settled is not None:
        return view.settled.value
    if not view.tracked:
        return NOT_TRACKED
    if view.live_value is None and view.state is State.UNKNOWN:
        return ("no stat line yet" if leg.market_type.value.startswith("player_")
                else "waiting for a score")
    words = SPREAD_WORDS if leg.market_type is MarketType.ALT_SPREAD else STATE_WORDS
    return f"{fmt_value(view.live_value)} · {words.get(view.state, 'level')}"


def espn_unavailable_text(since: datetime, tz: ZoneInfo) -> str:
    return (f"Live data unavailable since {fmt_time(since, tz)} (ESPN is blocking or erroring). "
            "Results will still settle after the game.")


# --- Analytics (parlaytracker app/pages/analytics.py) --------------------------------------------

LOW_SAMPLE = "low sample"


def pct(value: float | None, digits: int = 1) -> str:
    return DASH if value is None else f"{value * 100:.{digits}f}%"


def signed(value: float | None, unit: str, digits: int = 1) -> str:
    return DASH if value is None else f"{value:+.{digits}f} {unit}"


def hit_rate_text(s: SelectionStats) -> str:
    """60.0% (42–75%): the interval always sits next to the rate (section 10.3)."""
    if s.hit_rate is None:
        return DASH
    low, high = s.interval
    return f"{pct(s.hit_rate)} ({low * 100:.0f}–{high * 100:.0f}%)"


def evidence_text(s: SelectionStats) -> str:
    beats = s.interval_beats_break_even
    if beats is None:
        return DASH
    return "interval above break-even" if beats else "not yet evidence of an edge"


def clv_text(mean: Mean, unit: str, digits: int) -> str:
    return DASH if mean.value is None else f"{signed(mean.value, unit, digits)} (n {mean.n})"
