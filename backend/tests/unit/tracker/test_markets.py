# Ported from parlaytracker@c3bd43c tests/unit/test_config_and_markets.py: the markets test only.
# Its settings tests come with the settings themselves (Phases 2 and 3).
from juju.tracker.markets import markets_for
from juju.tracker.models import MarketType, Sport


def test_markets_per_sport():
    assert MarketType.PLAYER_RECEPTIONS in markets_for(Sport.NFL)
    assert MarketType.PLAYER_POINTS not in markets_for(Sport.NFL)
    assert MarketType.PLAYER_POINTS in markets_for(Sport.NBA)
    assert MarketType.PLAYER_POINTS in markets_for(Sport.NHL)
    assert not [m for m in markets_for(Sport.MLB) if m.value.startswith("player_")]
    for sport in Sport:
        assert {MarketType.GAME_TOTAL, MarketType.OTHER} <= set(markets_for(sport))
