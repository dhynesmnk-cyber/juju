# Ported from parlaytracker@c3bd43c parlaytracker/ingest/nflverse.py: `stat_value`,
# `offense_snaps`, `player_mapped` and `warm`. Changes: they sit on Juju's streaming client (no
# nflreadpy or polars), whose rows are strings ("" or "NA" for an empty column), and the snap
# counts and Pro Football Reference ids are Juju's `snap_counts` and `player_ids` datasets.
"""nflverse for the tracker's NFL legs: a market's value, and offensive snaps.

Players map only through the ID columns (ESPN id -> gsis_id -> stat lines; ESPN id -> pfr_id ->
snap counts), never by name.
"""
from decimal import Decimal

from juju.ingest.nflverse import NflverseData
from juju.tracker.models import MarketType

# Which weekly-stats columns settle which market (summed when there are several).
MARKET_COLUMNS: dict[MarketType, tuple[str, ...]] = {
    MarketType.PLAYER_RECEPTIONS: ("receptions",),
    MarketType.PLAYER_RECEIVING_YARDS: ("receiving_yards",),
    MarketType.PLAYER_RUSHING_YARDS: ("rushing_yards",),
    MarketType.PLAYER_PASSING_YARDS: ("passing_yards",),
    MarketType.PLAYER_PASS_COMPLETIONS: ("completions",),
    MarketType.PLAYER_INTERCEPTIONS: ("passing_interceptions",),
    MarketType.PLAYER_FIELD_GOALS: ("fg_made",),
    MarketType.PLAYER_TOUCHDOWNS: ("rushing_tds", "receiving_tds", "special_teams_tds", "def_tds"),
}
EMPTY = ("", "NA")
# Everything the canary loads: the weekly stats, both player id maps, and the snap counts. Not
# the play-by-play (about 30 MB gzipped): the tracker doesn't read it.
WARM = ("schedules", "players", "player_stats", "player_ids", "snap_counts")


class NflverseLegs(NflverseData):
    """Juju's nflverse data, with the lookups the tracker's legs need."""

    def warm(self, season: int) -> None:
        """Load everything the tracker reads (the canary): a failure opens the breaker."""
        for dataset in WARM:
            self.load(dataset, season)

    def player_mapped(self, espn_athlete_id: str) -> bool:
        return self.gsis_id(espn_athlete_id) is not None

    def stat_value(self, season: int, espn_event_id: str, espn_athlete_id: str,
                   market: MarketType) -> Decimal | None:
        """The player's stat in that game, or None: no such game, player not mapped, or no
        stat line. An empty column on his line counts as 0, but a line with every one of the
        market's columns empty is no stat line at all."""
        gsis = self.gsis_id(espn_athlete_id)
        if gsis is None:
            return None
        row = self.game_stats(season, espn_event_id).get(gsis)
        if row is None:
            return None
        present = [row[c] for c in MARKET_COLUMNS[market] if row[c] not in EMPTY]
        return sum((Decimal(v) for v in present), Decimal(0)) if present else None

    def offense_snaps(self, season: int, espn_event_id: str,
                      espn_athlete_id: str) -> float | None:
        """Offensive snaps, or None when there is no snap-count row (not published, or the
        player isn't mapped)."""
        game_id = self.game_id(season, espn_event_id)
        player = self._index("player_ids", 0, "espn_id").get(str(espn_athlete_id))
        pfr = player["pfr_id"] if player else ""
        if game_id is None or not pfr:
            return None
        for row in self.load("snap_counts", season):
            if row["game_id"] == game_id and row["pfr_player_id"] == pfr:
                return None if row["offense_snaps"] in EMPTY else float(row["offense_snaps"])
        return None
