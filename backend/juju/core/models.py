"""Database models. Timestamps are UTC (`timestamptz`); money and lines are Decimal.

The archive (`odds_snapshots`, `prices`) is append-only: a price is never updated or deleted,
so every number a card shows can be traced to the payload it came from.
"""
import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint, DateTime, Enum, ForeignKey, Index, LargeBinary, MetaData, Numeric, String,
    Text, UniqueConstraint, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from juju.core.enums import (
    DataSource, EventStatus, FailureKind, HealthState, SnapshotSource, Stat,
)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    })


def _enum(e: type[enum.Enum], name: str) -> Enum:
    # VARCHAR + CHECK instead of a native Postgres ENUM (as in parlaytracker): adding a value
    # later is a one-line constraint migration.
    return Enum(e, name=name, native_enum=False, create_constraint=True, length=32,
                values_callable=lambda members: [m.value for m in members])


TZ = DateTime(timezone=True)


class Game(Base):
    """One NFL game, known to The Odds API, to ESPN, or (normally) both."""
    __tablename__ = "games"
    id: Mapped[int] = mapped_column(primary_key=True)
    odds_event_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    espn_event_id: Mapped[str | None] = mapped_column(String(20), unique=True)
    home_team: Mapped[str] = mapped_column(String(60))  # full name, e.g. "Chicago Bears"
    away_team: Mapped[str] = mapped_column(String(60))
    home_abbr: Mapped[str | None] = mapped_column(String(6))
    away_abbr: Mapped[str | None] = mapped_column(String(6))
    home_espn_id: Mapped[str | None] = mapped_column(String(10))
    away_espn_id: Mapped[str | None] = mapped_column(String(10))
    # Scheduled kickoff. T-45 is always computed from the current value (docs/GOALS.md 2.3).
    commence_time: Mapped[datetime] = mapped_column(TZ, index=True)
    status: Mapped[EventStatus] = mapped_column(
        _enum(EventStatus, "event_status"), default=EventStatus.SCHEDULED)
    home_score: Mapped[int | None]
    away_score: Mapped[int | None]
    period: Mapped[int | None]
    clock_seconds: Mapped[int | None]
    final_at: Mapped[datetime | None] = mapped_column(TZ)
    last_polled_at: Mapped[datetime | None] = mapped_column(TZ)     # game state last read
    last_progress_at: Mapped[datetime | None] = mapped_column(TZ)   # progress key last advanced
    last_box_at: Mapped[datetime | None] = mapped_column(TZ)        # box score last read
    live_source: Mapped[DataSource | None] = mapped_column(_enum(DataSource, "live_source"))
    # Set when a player's stat changed after the game had settled (final + 10 min); what
    # changed is in `stat_corrections`.
    corrected_at: Mapped[datetime | None] = mapped_column(TZ)
    # Set when the next-day check against nflverse has run for this game (worker/verify.py).
    verified_at: Mapped[datetime | None] = mapped_column(TZ)
    last_error: Mapped[str | None] = mapped_column(Text)

    @property
    def label(self) -> str:
        return f"{self.away_abbr or self.away_team} @ {self.home_abbr or self.home_team}"


class Player(Base):
    """A rostered NFL player, keyed by ESPN's athlete id."""
    __tablename__ = "players"
    espn_athlete_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    team_espn_id: Mapped[str | None] = mapped_column(String(10), index=True)
    position: Mapped[str | None] = mapped_column(String(10))
    unavailable: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())


class PlayerNameMap(Base):
    """How an Odds API player name was matched to a roster player, per game. Unmatched names
    are kept here (and shown by `cli unmapped`), never silently dropped."""
    __tablename__ = "player_name_map"
    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    raw_name: Mapped[str] = mapped_column(String(100))
    espn_athlete_id: Mapped[str | None] = mapped_column(String(20))
    score: Mapped[float | None]
    status: Mapped[str] = mapped_column(String(10))  # "auto", "doubtful" or "unmapped"

    __table_args__ = (
        UniqueConstraint("game_id", "raw_name", name="uq_player_name_map_game_raw"),
        CheckConstraint("status IN ('auto', 'doubtful', 'unmapped')", name="status_valid"),
    )


class OddsSnapshot(Base):
    """One response from The Odds API for one game: the provenance of every price in it."""
    __tablename__ = "odds_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    source: Mapped[SnapshotSource] = mapped_column(_enum(SnapshotSource, "snapshot_source"))
    slot: Mapped[str] = mapped_column(String(10))  # "t180", "t60", "t49", "t47", "t45", "repair"
    # The moment these prices were the book's prices: our fetch time for a live capture, the
    # vendor's snapshot time for a historical one. T-45 selection uses this.
    observed_at: Mapped[datetime] = mapped_column(TZ)
    fetched_at: Mapped[datetime] = mapped_column(TZ)
    vendor_ts: Mapped[datetime | None] = mapped_column(TZ)
    commence_time: Mapped[datetime] = mapped_column(TZ)  # the kickoff the vendor listed then
    markets: Mapped[str] = mapped_column(Text)  # requested, comma-separated
    books: Mapped[str] = mapped_column(Text)
    cost: Mapped[int | None]  # x-requests-last
    payload_sha256: Mapped[str] = mapped_column(String(64))
    payload_gzip: Mapped[bytes] = mapped_column(LargeBinary)

    prices: Mapped[list["Price"]] = relationship(back_populates="snapshot")

    __table_args__ = (Index("ix_odds_snapshots_game_observed", "game_id", "observed_at"),)


class Price(Base):
    """One outcome's price in one snapshot, exactly as the vendor gave it."""
    __tablename__ = "prices"
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("odds_snapshots.id", ondelete="CASCADE"))
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    book: Mapped[str] = mapped_column(String(40))
    market_key: Mapped[str] = mapped_column(String(60))
    market_last_update: Mapped[datetime | None] = mapped_column(TZ)
    outcome_name: Mapped[str] = mapped_column(String(100))  # Over/Under/Yes/No or a team
    description: Mapped[str | None] = mapped_column(String(100))  # the player, as written
    espn_athlete_id: Mapped[str | None] = mapped_column(String(20))
    point: Mapped[Decimal | None] = mapped_column(Numeric(6, 1))
    american: Mapped[int]

    snapshot: Mapped[OddsSnapshot] = relationship(back_populates="prices")

    __table_args__ = (
        CheckConstraint("american >= 100 OR american <= -100", name="odds_valid"),
        Index("ix_prices_lookup", "game_id", "market_key", "espn_athlete_id"),
        Index("ix_prices_snapshot", "snapshot_id"),
    )


class LiveStat(Base):
    """A player's current value for one stat in one game, from the box score. A player who
    appears anywhere in the box score has played, so his missing stats are zero; a player who
    appears nowhere has no row at all ("no stat line yet", never zero)."""
    __tablename__ = "live_stats"
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"),
                                         primary_key=True)
    espn_athlete_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    stat: Mapped[Stat] = mapped_column(_enum(Stat, "stat"), primary_key=True)
    value: Mapped[Decimal] = mapped_column(Numeric(8, 1))
    source: Mapped[DataSource] = mapped_column(_enum(DataSource, "stat_source"))
    updated_at: Mapped[datetime] = mapped_column(TZ)
    # Set when nflverse gave the same value (or this value replaced the feed's, see `source`).
    verified_at: Mapped[datetime | None] = mapped_column(TZ)


class StatCorrection(Base):
    """A player's stat that changed after his game had settled: a later box-score read, or the
    next-day check against nflverse. Cards say what changed (docs/GOALS.md section 5)."""
    __tablename__ = "stat_corrections"
    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    espn_athlete_id: Mapped[str] = mapped_column(String(20))
    stat: Mapped[Stat] = mapped_column(_enum(Stat, "correction_stat"))
    old_value: Mapped[Decimal | None] = mapped_column(Numeric(8, 1))  # None: no stat line
    new_value: Mapped[Decimal] = mapped_column(Numeric(8, 1))
    source: Mapped[DataSource] = mapped_column(_enum(DataSource, "correction_source"))
    corrected_at: Mapped[datetime] = mapped_column(TZ)


class Play(Base):
    """A notable play (a score, or a gain of 20+ yards), for the "Live now" chips."""
    __tablename__ = "plays"
    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    espn_play_id: Mapped[str] = mapped_column(String(40), unique=True)
    sequence: Mapped[int]
    period: Mapped[int | None]
    clock: Mapped[str | None] = mapped_column(String(8))
    wallclock: Mapped[datetime | None] = mapped_column(TZ)
    kind: Mapped[str] = mapped_column(String(20))  # a PlayKind value
    label: Mapped[str] = mapped_column(String(80))  # "S. Barkley 60-yd TD run"
    yards: Mapped[int | None]
    text: Mapped[str] = mapped_column(Text)
    espn_athlete_id: Mapped[str | None] = mapped_column(String(20))
    team_espn_id: Mapped[str | None] = mapped_column(String(10))
    scoring: Mapped[bool] = mapped_column(default=False)


class DecidingPlay(Base):
    """The play on which a player's stat went past an archived line (worker/deciding.py).
    `exact` is False for the live feed's "on or around" (the play may be missing, leaving only
    the game clock), True for nflverse's play-by-play the next day."""
    __tablename__ = "deciding_plays"
    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    espn_athlete_id: Mapped[str] = mapped_column(String(20))
    stat: Mapped[Stat] = mapped_column(_enum(Stat, "deciding_stat"))
    line: Mapped[Decimal] = mapped_column(Numeric(6, 1))
    exact: Mapped[bool]
    period: Mapped[int | None]
    clock: Mapped[str | None] = mapped_column(String(8))  # the game clock, "7:42"
    text: Mapped[str | None] = mapped_column(String(300))
    play_id: Mapped[int | None] = mapped_column(ForeignKey("plays.id", ondelete="SET NULL"))
    noted_at: Mapped[datetime] = mapped_column(TZ)

    __table_args__ = (Index("ix_deciding_plays_lookup", "game_id", "espn_athlete_id"),)


class HotGame(Base):
    """Games people are looking up right now: the live loop reads their box scores first."""
    __tablename__ = "hot_games"
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"),
                                         primary_key=True)
    last_lookup_at: Mapped[datetime] = mapped_column(TZ)


class Lookup(Base):
    """Anonymous usage, to see which input paths work. No IP address, no user agent."""
    __tablename__ = "lookups"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    path: Mapped[str] = mapped_column(String(20))  # deterministic, llm, choices, none
    text: Mapped[str] = mapped_column(String(200))
    milliseconds: Mapped[int]
    result: Mapped[str | None] = mapped_column(String(80))


class SourceHealth(Base):
    """One row per provider (and "worker", "archive"): what the health report and banner read.
    Same shape as parlaytracker's."""
    __tablename__ = "source_health"
    source: Mapped[str] = mapped_column(String(30), primary_key=True)
    state: Mapped[HealthState] = mapped_column(
        _enum(HealthState, "health_state"), default=HealthState.OK)
    failure_kind: Mapped[FailureKind | None] = mapped_column(_enum(FailureKind, "failure_kind"))
    open_until: Mapped[datetime | None] = mapped_column(TZ)
    last_success_at: Mapped[datetime | None] = mapped_column(TZ)
    last_failure_at: Mapped[datetime | None] = mapped_column(TZ)
    consecutive_failures: Mapped[int] = mapped_column(default=0)
    requests_last_hour: Mapped[int] = mapped_column(default=0)
    errors_last_hour: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    quota_remaining: Mapped[int | None]
