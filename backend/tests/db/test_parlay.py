"""Same-game parlays through the internal API, on the recorded PHI @ CHI game."""
from decimal import Decimal as D

import pytest

from juju.core.odds import parlay_decimal, payout
from juju.core.parlay import SGP_NOTE
from tests.db.test_api import card, client, seed  # noqa: F401 (the fixture)
from tests.support import BARKLEY, SMITH

pytestmark = pytest.mark.db

CHI = "3"
WINNERS = f"p-{SMITH}-player_receptions,p-{BARKLEY}-player_rush_yds,t-{CHI}-spreads"


def keys_in(obj) -> set[str]:
    if isinstance(obj, dict):
        return set(obj) | {k for v in obj.values() for k in keys_in(v)}
    if isinstance(obj, list):
        return {k for v in obj for k in keys_in(v)}
    return set()


def test_a_parlay_that_won_pays_the_product_of_its_legs_at_one_book(client, db):  # noqa: F811
    game = seed(db, "final")
    r = client.get(f"/api/parlay/{game}", params={"legs": WINNERS})
    assert r.status_code == 200 and r.headers["cache-control"].startswith("public, s-maxage=300")
    view = r.json()
    assert view["outcome"] == "won" and view["book_name"] == "DraftKings"
    assert [leg["outcome"] for leg in view["legs"]] == ["won", "won", "won"]
    assert {leg["book_name"] for leg in view["legs"]} == {"DraftKings"}
    # The same prices the single cards show, multiplied: nothing new is invented.
    singles = [card(client.get(f"/api/player/{game}/{SMITH}").json(), "player_receptions"),
               card(client.get(f"/api/player/{game}/{BARKLEY}").json(), "player_rush_yds"),
               card(client.get(f"/api/team/{game}/{CHI}").json(), "spreads")]
    assert all(s["price"]["book_name"] == "DraftKings" for s in singles)
    expected = payout(D(10), parlay_decimal(s["price"]["american"] for s in singles))
    assert D(view["returns"]) == expected and view["headline"] == f"$10 → ${expected}"
    assert view["notes"][0] == SGP_NOTE and view["legs_counted"] == 3
    assert view["disclaimer"].startswith("Hypothetical")


def test_no_leg_price_is_listed(client, db):  # noqa: F811
    """docs/licensing.md: a parlay shows derived values only; each price stays on its card."""
    game = seed(db, "final")
    view = client.get(f"/api/parlay/{game}", params={"legs": WINNERS}).json()
    assert not keys_in(view) & {"american", "price", "prices", "others", "point"}


def test_one_lost_leg_loses_it(client, db):  # noqa: F811
    game = seed(db, "final")
    legs = f"p-{SMITH}-player_reception_yds,p-{BARKLEY}-player_rush_yds"  # 65 < 71.5
    view = client.get(f"/api/parlay/{game}", params={"legs": legs}).json()
    assert view["outcome"] == "lost" and view["headline"] == "$10 → $0.00"


def test_live_it_is_cashed_only_when_every_leg_is(client, db):  # noqa: F811
    game = seed(db, "live")
    locked = f"p-{SMITH}-player_receptions,p-{BARKLEY}-player_rush_yds"
    view = client.get(f"/api/parlay/{game}", params={"legs": locked}).json()
    assert view["outcome"] == "locked" and "if it hits" not in view["headline"]
    live = f"{locked},p-{SMITH}-player_reception_yds"  # 65 of 71.5 so far
    view = client.get(f"/api/parlay/{game}", params={"legs": live}).json()
    assert view["outcome"] == "live" and view["headline"].endswith("if it hits")
    assert [leg["outcome"] for leg in view["legs"]] == ["locked", "locked", "live"]
    assert view["legs"][2]["needed"] == "7"


def test_a_ladder_leg_at_its_threshold(client, db):  # noqa: F811
    game = seed(db, "final")
    legs = f"p-{SMITH}-player_reception_yds_alternate-59.5,p-{BARKLEY}-player_rush_yds"
    view = client.get(f"/api/parlay/{game}", params={"legs": legs}).json()
    assert view["legs"][0]["bet"] == "60+ receiving yards" and view["outcome"] == "won"


def test_a_leg_without_a_price_leaves_the_parlay_unpriced(client, db):  # noqa: F811
    game = seed(db, "final")
    legs = f"p-{SMITH}-player_receptions,p-{BARKLEY}-player_pass_yds"  # never priced
    view = client.get(f"/api/parlay/{game}", params={"legs": legs}).json()
    assert view["headline"] == "No price on file for every leg" and view["returns"] is None
    assert view["legs"][1]["no_price"].startswith("No price on file")


@pytest.mark.parametrize(("legs", "status"), [
    (f"p-{SMITH}-player_receptions", 422),                     # one leg
    (f"p-{SMITH}-player_receptions,p-{SMITH}-player_receptions", 422),
    ("drop table", 422),
    (f"p-{SMITH}-player_receptions,p-999999-player_receptions", 404),   # nobody
    (f"p-{SMITH}-player_receptions,t-99-spreads", 404),                 # not in this game
])
def test_bad_parlays_are_refused(client, db, legs, status):  # noqa: F811
    game = seed(db, "final")
    assert client.get(f"/api/parlay/{game}", params={"legs": legs}).status_code == status
    assert client.get("/api/parlay/99999", params={"legs": WINNERS}).status_code == 404
