"""What a card says when it has no price, and that it never says "None" (api/views.py)."""
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.encoders import jsonable_encoder
from sqlalchemy import delete
from sqlalchemy.orm import Session

from juju.api.views import (
    LEAN_TEXT, NO_PLAYER_PRICES, NO_PRICE_TEXT, _bet_text, player_view, team_view,
)
from juju.config import DEFAULT_BOOK_CHAIN
from juju.core.budget import NO_CREDITS_NOTE
from juju.core.markets import CATALOG
from juju.core.models import Game, OddsSnapshot
from juju.core.t45 import NoPrice
from juju.dev_seed import seed_phi_chi_scenario
from tests.support import BARKLEY

pytestmark = pytest.mark.db

BEARS = "3"
BACKUP_QB = "4434153"  # Tyson Bagent: rostered, never priced in the recording


def _game(db, *, snapshots: bool = True, plan: str | None = None, note: str | None = None):
    now = datetime.now(tz=UTC)
    with Session(db, expire_on_commit=False) as s:
        game = seed_phi_chi_scenario(s, "live", now)
        if not snapshots:
            s.execute(delete(OddsSnapshot).where(OddsSnapshot.game_id == game.id))
        game.capture_plan, game.capture_note = plan, note
        s.commit()
    return game.id, now


def _cards(db, game_id, now):
    with Session(db) as s:
        game = s.get(Game, game_id)
        return {c.key: c for c in team_view(s, game, BEARS, now, DEFAULT_BOOK_CHAIN).cards}


def test_a_market_the_capture_didnt_ask_for_says_so(db):
    game_id, now = _game(db)  # the recording has no moneyline
    assert _cards(db, game_id, now)["h2h"].no_price == NO_PRICE_TEXT[NoPrice.NOT_SAVED]


def test_on_a_lean_plan_it_says_the_credits_were_short(db):
    game_id, now = _game(db, plan="lean")
    assert _cards(db, game_id, now)["h2h"].no_price == LEAN_TEXT


def test_a_game_with_no_odds_says_why_when_the_worker_knew(db):
    game_id, now = _game(db, snapshots=False, plan="none", note=NO_CREDITS_NOTE)
    cards = _cards(db, game_id, now)
    assert {c.no_price for c in cards.values()} == {f"No price on file: {NO_CREDITS_NOTE}"}
    game_id, now = _game(db, snapshots=False)
    assert {c.no_price for c in _cards(db, game_id, now).values()} == {
        NO_PRICE_TEXT[NoPrice.NO_SNAPSHOT]}


def test_a_player_page_with_no_cards_says_why(db):
    game_id, now = _game(db)
    with Session(db) as s:
        game = s.get(Game, game_id)
        view = player_view(s, game, BACKUP_QB, now, DEFAULT_BOOK_CHAIN)
        assert view is not None and view.cards == [] and view.no_price == NO_PLAYER_PRICES
        assert player_view(s, game, BARKLEY, now, DEFAULT_BOOK_CHAIN).no_price is None
    game_id, now = _game(db, snapshots=False, note=NO_CREDITS_NOTE)
    with Session(db) as s:
        view = player_view(s, s.get(Game, game_id), BARKLEY, now, DEFAULT_BOOK_CHAIN)
        assert view.cards == [] and view.no_price == f"No price on file: {NO_CREDITS_NOTE}"


def test_no_card_ever_says_none(db):
    game_id, now = _game(db, snapshots=False)
    cards = _cards(db, game_id, now)
    text = json.dumps(jsonable_encoder(list(cards.values()), custom_encoder={Decimal: str}))
    assert "None" not in text and "null points" not in text
    for market in CATALOG:
        assert "None" not in _bet_text(market, None, "CHI")
