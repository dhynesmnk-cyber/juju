"""The play that decided it (docs/GOALS.md section 10, M3): the play on which a player's stat
went past a line Juju has a price for.

Two sources, never mixed up on the card:
- Live, from the box score (`note_crossings`, called by the live loop). A read that shows a stat
  past a line may cover several plays, and ESPN lists only notable ones, so the card says "on or
  around": the latest notable play by him, of a kind that moves that stat, among the plays that
  arrived in the same read. None of those (or it is the game's first read, whose window has no
  start): the game clock at that read, and no play.
- Next day, from nflverse's play-by-play (`exact_plays`, called by the verification job). It is
  exact, and shown only if the play-by-play adds up to the final stat the card settled on.
"""
import logging
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from juju.core.enums import EventStatus, PlayKind, Stat
from juju.core.markets import BY_KEY, CATALOG, OVER, YES, Scope
from juju.core.models import DecidingPlay, Game, LiveStat, Play, Price
from juju.ingest.nflverse import MAX_STATS, PLAY_STATS, play_value

log = logging.getLogger("juju.deciding")

YES_LINE = Decimal("0.5")  # a "Yes" bet (anytime TD) is Over 0.5, as the cards price it

# The kinds of play that can move each stat: every market on it, and what touches it.
STAT_KINDS: dict[Stat, frozenset[PlayKind]] = defaultdict(frozenset)
for _m in CATALOG:
    if _m.stat is not None:
        STAT_KINDS[_m.stat] = STAT_KINDS[_m.stat] | _m.touched_by


def archived_lines(session: Session, game_id: int) -> dict[tuple[str, Stat], set[Decimal]]:
    """Every Over (or Yes) line archived for a player in this game, by (athlete, stat)."""
    out: dict[tuple[str, Stat], set[Decimal]] = defaultdict(set)
    rows = session.execute(select(Price.espn_athlete_id, Price.market_key, Price.outcome_name,
                                  Price.point).where(
        Price.game_id == game_id, Price.espn_athlete_id.is_not(None)).distinct())
    for athlete, key, outcome, point in rows:
        market = BY_KEY.get(key)
        if (market is None or market.scope is not Scope.PLAYER or market.stat is None
                or market.first_td):
            continue
        if market.yes_only and outcome in (YES, OVER):
            out[(athlete, market.stat)].add(YES_LINE)
        elif not market.yes_only and outcome == OVER and point is not None:
            out[(athlete, market.stat)].add(point)
    return out


def crossed(before: Decimal | None, after: Decimal, line: Decimal) -> bool:
    """Did the stat go past the line (an Over wins on more than the line)?"""
    return (before is None or before <= line) and line < after


def total(values: Sequence[Decimal | None], stat: Stat) -> Decimal:
    """A stat from its plays: the sum, or the longest (0 with no plays, as ESPN has it)."""
    counted = [v for v in values if v is not None]
    if stat in MAX_STATS:
        return max(counted, default=Decimal(0))
    return sum(counted, Decimal(0))


def deciding_index(values: Sequence[Decimal | None], stat: Stat, line: Decimal) -> int | None:
    """The play after which the stat stayed past the line: the last crossing (yards can go
    back under it on a loss), or None if it ends at or under the line."""
    longest = stat in MAX_STATS
    running: Decimal | None = None if longest else Decimal(0)
    found = None
    for i, value in enumerate(values):
        if value is None:
            continue
        if longest:
            new = value if running is None else max(running, value)
        else:
            assert running is not None
            new = running + value
        if crossed(running, new, line):
            found = i
        elif new <= line:
            found = None
        running = new
    return found


def _clock(seconds: int | None) -> str | None:
    return None if seconds is None else f"{seconds // 60}:{seconds % 60:02d}"


def _save(session: Session, game: Game, athlete: str, stat: Stat, line: Decimal, exact: bool,
          now: datetime, **fields) -> None:
    """One row per (player, stat, line) and source; a later live crossing replaces the earlier
    one (he went back under the line and past it again)."""
    row = session.scalars(select(DecidingPlay).where(
        DecidingPlay.game_id == game.id, DecidingPlay.espn_athlete_id == athlete,
        DecidingPlay.stat == stat, DecidingPlay.line == line,
        DecidingPlay.exact.is_(exact))).first()
    if row is None:
        row = DecidingPlay(game_id=game.id, espn_athlete_id=athlete, stat=stat, line=line,
                           exact=exact)
        session.add(row)
    row.period, row.clock = fields.get("period"), fields.get("clock")
    row.text, row.play_id, row.noted_at = fields.get("text"), fields.get("play_id"), now


# --- Live ---------------------------------------------------------------------------------------


def stat_values(session: Session, game_id: int) -> dict[tuple[str, Stat], Decimal]:
    return {(r.espn_athlete_id, r.stat): r.value for r in session.scalars(
        select(LiveStat).where(LiveStat.game_id == game_id))}


def note_crossings(session: Session, game: Game, before: dict[tuple[str, Stat], Decimal],
                   new_plays: Sequence[Play], first_read: bool, now: datetime,
                   settled: bool = False) -> int:
    """After a box-score read: record each archived line a stat went past. Returns how many.
    Nothing after the game has settled: a change then is a correction, not a play."""
    if settled or game.status in (EventStatus.SCHEDULED, EventStatus.POSTPONED,
                                  EventStatus.CANCELLED):
        return 0
    after = stat_values(session, game.id)
    moved = {k: v for k, v in after.items() if before.get(k, Decimal(0)) != v}
    if not moved:
        return 0
    lines = archived_lines(session, game.id)
    noted = 0
    for (athlete, stat), value in moved.items():
        old = before.get((athlete, stat), Decimal(0))
        for line in sorted(lines.get((athlete, stat), ())):
            if not crossed(old, value, line):
                continue
            play = None if first_read else max(
                (p for p in new_plays if p.espn_athlete_id == athlete
                 and PlayKind(p.kind) in STAT_KINDS[stat]),
                key=lambda p: (p.period or 0, p.sequence), default=None)
            if play is not None:
                _save(session, game, athlete, stat, line, False, now, period=play.period,
                      clock=play.clock, text=play.label, play_id=play.id)
            else:
                _save(session, game, athlete, stat, line, False, now, period=game.period,
                      clock=_clock(game.clock_seconds))
            noted += 1
    return noted


# --- Next day -----------------------------------------------------------------------------------


def _pbp_clock(text: str) -> str | None:
    """nflverse's "08:56" as a game clock, "8:56"."""
    minutes, _, seconds = text.partition(":")
    return f"{int(minutes)}:{seconds}" if minutes.isdigit() and seconds else None


def _desc(text: str) -> str:
    """nflverse's description without its leading clock: "(8:56) (Shotgun) 11-C.Keenum ..."."""
    if text.startswith("(") and ")" in text:
        head, _, rest = text.partition(")")
        if ":" in head:
            text = rest.strip()
    return text[:300]


def exact_plays(session: Session, game: Game, plays: Sequence[dict[str, str]],
                gsis_of: Callable[[str], str | None], now: datetime) -> int:
    """From nflverse's play-by-play: the exact play that decided each winning Over. Returns how
    many were found."""
    if not plays:
        return 0
    finals = stat_values(session, game.id)
    found = 0
    for (athlete, stat), lines in archived_lines(session, game.id).items():
        final = finals.get((athlete, stat))
        gsis = gsis_of(athlete)
        if stat not in PLAY_STATS or final is None or gsis is None:
            continue
        values = [play_value(row, gsis, stat) for row in plays]
        if total(values, stat) != final:
            log.info("%s: %s %s play-by-play gives %s, not %s: no exact play", game.label,
                     athlete, stat, total(values, stat), final)
            continue
        for line in lines:
            i = deciding_index(values, stat, line) if final > line else None
            if i is None:
                continue
            row = plays[i]
            _save(session, game, athlete, stat, line, True, now, period=int(row["qtr"]),
                  clock=_pbp_clock(row["time"]), text=_desc(row["desc"]))
            found += 1
    return found


def check_longest(session: Session, game: Game, plays: Sequence[dict[str, str]],
                  gsis_of: Callable[[str], str | None], now: datetime) -> int:
    """The longest rush and catch aren't in nflverse's weekly stats, but the play-by-play has
    every play. Where it gives the feed's number, that number is verified. Where it doesn't,
    the feed's number stays, unverified: a longest play worked out from the play-by-play is
    derived, not an official column, so it never corrects anything. Returns how many."""
    if not plays:
        return 0
    verified = 0
    for row in session.scalars(select(LiveStat).where(
            LiveStat.game_id == game.id, LiveStat.stat.in_(MAX_STATS))):
        gsis = gsis_of(row.espn_athlete_id)
        if gsis is None:
            continue
        if total([play_value(p, gsis, row.stat) for p in plays], row.stat) == row.value:
            row.verified_at = now
            verified += 1
        else:
            log.info("%s: %s %s is %s in the feed but not in the play-by-play", game.label,
                     row.espn_athlete_id, row.stat, row.value)
    return verified
