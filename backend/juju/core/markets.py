"""The markets Juju captures and prices: The Odds API's key, what the bet is, the stat that
decides it, and which kinds of play touch it (docs/GOALS.md sections 4, 5 and 7).

Keys are from https://the-odds-api.com/sports-odds-data/betting-markets.html. On 2026-09-30 one
real call for PIT @ CLE with all 34 keys and all 10 books was accepted with no 422 (29 markets
came back, 29 credits; tests/fixtures/odds_api/nfl_event_odds_2026-10-01_PIT-CLE_all_markets.json).
Not offered for that game, so still unseen: player_rush_tds, player_reception_tds, player_sacks,
player_tackles_assists, player_defensive_interceptions. A rejected key is found and dropped by
`ingest/odds_api.py` and the capture jobs, so the rest of a capture never depends on one key.
TD-scorer markets come back as "Yes" only, with no line; `player_tds_over` as "Over" only.
"""
import enum
from dataclasses import dataclass

from juju.core.enums import PlayKind as P, Stat


class Scope(enum.StrEnum):
    PLAYER = "player"  # Over (or Yes) on one player's stat
    TOTAL = "total"    # Over on the game's combined score
    TEAM = "team"      # Over on one team's score
    SPREAD = "spread"  # one side, with a handicap
    MONEYLINE = "moneyline"  # one side, to win


@dataclass(frozen=True)
class Market:
    key: str
    label: str
    scope: Scope
    stat: Stat | None = None
    yes_only: bool = False     # "Yes" outcomes only: no other side, so no fair value
    alternate: bool = False    # a ladder of lines ("100+ yards")
    first_td: bool = False     # settled by who scores the game's first touchdown
    touched_by: frozenset[P] = frozenset()

    @property
    def tracked(self) -> bool:
        """Can Juju follow this bet live? A player market needs a box-score stat."""
        return self.scope is not Scope.PLAYER or self.stat is not None or self.first_td


_TD = frozenset({P.TOUCHDOWN})
_RUN = frozenset({P.RUN, P.TOUCHDOWN})
_CATCH = frozenset({P.CATCH, P.TOUCHDOWN})
_PASS = frozenset({P.PASS, P.PASS_TD})
_TEAM = frozenset({P.TOUCHDOWN, P.PASS_TD, P.FIELD_GOAL})

CATALOG: tuple[Market, ...] = (
    # Game and team markets. `spreads`, `totals`, `team_totals` and their alternates are
    # VERIFIED (parlaytracker, 2026-09-28).
    Market("h2h", "Moneyline", Scope.MONEYLINE, touched_by=_TEAM),
    Market("spreads", "Spread", Scope.SPREAD, touched_by=_TEAM),
    Market("totals", "Game total", Scope.TOTAL, touched_by=_TEAM),
    Market("team_totals", "Team total", Scope.TEAM, touched_by=_TEAM),
    Market("alternate_spreads", "Alt spread", Scope.SPREAD, alternate=True, touched_by=_TEAM),
    Market("alternate_totals", "Alt game total", Scope.TOTAL, alternate=True, touched_by=_TEAM),
    Market("alternate_team_totals", "Alt team total", Scope.TEAM, alternate=True,
           touched_by=_TEAM),
    # Scoring
    Market("player_anytime_td", "Anytime TD", Scope.PLAYER, Stat.TOUCHDOWNS, yes_only=True,
           touched_by=_TD),
    Market("player_1st_td", "First TD", Scope.PLAYER, yes_only=True, first_td=True,
           touched_by=_TD),
    Market("player_tds_over", "Touchdowns", Scope.PLAYER, Stat.TOUCHDOWNS, touched_by=_TD),
    # Rushing. `player_rush_yds` (+ alternate) VERIFIED.
    Market("player_rush_yds", "Rush yards", Scope.PLAYER, Stat.RUSHING_YARDS, touched_by=_RUN),
    Market("player_rush_longest", "Longest rush", Scope.PLAYER, Stat.LONGEST_RUSH,
           touched_by=_RUN),
    Market("player_rush_attempts", "Rush attempts", Scope.PLAYER, Stat.RUSH_ATTEMPTS,
           touched_by=_RUN),
    Market("player_rush_tds", "Rush TDs", Scope.PLAYER, Stat.RUSH_TDS, touched_by=_TD),
    # Receiving. `player_receptions`, `player_reception_yds` (+ alternates) VERIFIED.
    Market("player_reception_yds", "Receiving yards", Scope.PLAYER, Stat.RECEIVING_YARDS,
           touched_by=_CATCH),
    Market("player_reception_longest", "Longest reception", Scope.PLAYER,
           Stat.LONGEST_RECEPTION, touched_by=_CATCH),
    Market("player_receptions", "Receptions", Scope.PLAYER, Stat.RECEPTIONS, touched_by=_CATCH),
    Market("player_reception_tds", "Receiving TDs", Scope.PLAYER, Stat.RECEIVING_TDS,
           touched_by=_TD),
    # Passing. `player_pass_yds` (+ alternate) VERIFIED.
    Market("player_pass_yds", "Pass yards", Scope.PLAYER, Stat.PASSING_YARDS, touched_by=_PASS),
    Market("player_pass_tds", "Pass TDs", Scope.PLAYER, Stat.PASS_TDS,
           touched_by=frozenset({P.PASS_TD})),
    Market("player_pass_completions", "Completions", Scope.PLAYER, Stat.PASS_COMPLETIONS,
           touched_by=_PASS),
    Market("player_pass_attempts", "Pass attempts", Scope.PLAYER, Stat.PASS_ATTEMPTS,
           touched_by=_PASS),
    Market("player_pass_interceptions", "Interceptions thrown", Scope.PLAYER,
           Stat.INTERCEPTIONS_THROWN, touched_by=frozenset({P.INTERCEPTION})),
    # No box-score column gives the longest completion: priced, but not followed live.
    Market("player_pass_longest_completion", "Longest completion", Scope.PLAYER,
           touched_by=_PASS),
    # Kicking and defence
    Market("player_field_goals", "Field goals", Scope.PLAYER, Stat.FIELD_GOALS,
           touched_by=frozenset({P.FIELD_GOAL})),
    Market("player_kicking_points", "Kicking points", Scope.PLAYER, Stat.KICKING_POINTS,
           touched_by=frozenset({P.FIELD_GOAL})),
    Market("player_sacks", "Sacks", Scope.PLAYER, Stat.SACKS, touched_by=frozenset({P.SACK})),
    Market("player_tackles_assists", "Tackles + assists", Scope.PLAYER, Stat.TACKLES_ASSISTS),
    Market("player_defensive_interceptions", "Interceptions", Scope.PLAYER,
           Stat.DEF_INTERCEPTIONS, touched_by=frozenset({P.DEF_INTERCEPTION})),
    # Alternate (milestone) player lines: "100+ rushing yards" is Over 99.5.
    Market("player_rush_yds_alternate", "Rush yards", Scope.PLAYER, Stat.RUSHING_YARDS,
           alternate=True, touched_by=_RUN),
    Market("player_reception_yds_alternate", "Receiving yards", Scope.PLAYER,
           Stat.RECEIVING_YARDS, alternate=True, touched_by=_CATCH),
    Market("player_receptions_alternate", "Receptions", Scope.PLAYER, Stat.RECEPTIONS,
           alternate=True, touched_by=_CATCH),
    Market("player_pass_yds_alternate", "Pass yards", Scope.PLAYER, Stat.PASSING_YARDS,
           alternate=True, touched_by=_PASS),
    Market("player_pass_tds_alternate", "Pass TDs", Scope.PLAYER, Stat.PASS_TDS,
           alternate=True, touched_by=frozenset({P.PASS_TD})),
)

BY_KEY: dict[str, Market] = {m.key: m for m in CATALOG}

# What each capture asks for. The coverage check at T-3h takes the main markets only; the
# captures around T-45 take everything (docs/GOALS.md section 4).
MAIN_KEYS: tuple[str, ...] = tuple(m.key for m in CATALOG if not m.alternate)
ALL_KEYS: tuple[str, ...] = tuple(m.key for m in CATALOG)

# The outcome a card prices, and the opposite outcome used for fair value.
YES, NO, OVER, UNDER = "Yes", "No", "Over", "Under"


def priced_outcome(market: Market) -> str | None:
    """The outcome name to look up for this market, or None when it is a team name
    (spreads and moneylines are priced per side)."""
    if market.scope in (Scope.SPREAD, Scope.MONEYLINE):
        return None
    return YES if market.yes_only else OVER


def opposite(outcome_name: str) -> str | None:
    return {OVER: UNDER, YES: NO}.get(outcome_name)
