"""The tracker's API, `/api/me/*`: "Watch my parlays" for its two users
(docs/plans/integrate-parlaytracker.md sections 6.5 and 7).

Private twice over. The backend has no public address, and the site forwards a request here only
with a valid tracker cookie, naming its user in `x-juju-user` (web/app/api/me/[...path]/route.ts).
A name that isn't in TRACKER_USERS is refused, and with no users configured every route answers
404: /me is inert.

Slips carry stakes, so every answer is `no-store`. Leg closing lines are Odds API prices, so no
answer here lists them; the analytics use them only inside averages. The read models are built
here from the tracker's pure functions and its words (`juju/tracker/format.py`), so the site
never does betting maths.

ESPN: `/games` and `/roster` feed the Log form's pickers, and saving a slip looks its games up
again. These are the one place a person's request can reach a data provider besides
`/api/lookup`'s LLM fallback: through Juju's router, waiting at most 5 s for a rate-limit slot,
and cached (`juju/tracker/pickers.py`). The router's breakers here are in memory, as
parlaytracker's web app had them, so they never overwrite the worker's `source_health`.
"""
import threading
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from juju.api.deps import SessionDep, respond
from juju.config import get_settings
from juju.core.models import SourceHealth
from juju.ingest import espn
from juju.ingest.router import Breakers, EspnRouter
from juju.tracker import analytics as an
from juju.tracker import fetch, live, services
from juju.tracker import format as fmt
from juju.tracker.banner import health_banner
from juju.tracker.drafts import SlipDraft, draft_slip
from juju.tracker.markets import markets_for
from juju.tracker.models import Leg, LegResult, Slip, Sport, Sportsbook, Tag
from juju.tracker.pickers import WEB_MAX_WAIT, Pickers, game_label

LOCKED = "Sign in to Watch my parlays first."
KIND_TITLES = {
    "leg": "Leg needs review",
    "slip": "Slip needs review",
    "result": "Result needed",
    "closing_line": "Closing line missing",
}


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().display_tz)


def fail(status: int, detail: str) -> HTTPException:
    return HTTPException(status, detail, headers={"Cache-Control": "no-store"})


def tracker_user(x_juju_user: Annotated[str | None, Header()] = None) -> str:
    """The signed-in user's name as configured, or 404 (no users) / 401 (not one of them)."""
    users = get_settings().tracker_users
    if not users:
        raise fail(404, "Not Found")
    name = (x_juju_user or "").strip().lower()
    match = next((u for u in users if u.lower() == name), None)
    if match is None:
        raise fail(401, LOCKED)
    return match


UserDep = Annotated[str, Depends(tracker_user)]
router = APIRouter(prefix="/api/me", dependencies=[Depends(tracker_user)])


# --- ESPN, for the Log form ----------------------------------------------------------------------

_pickers: Pickers | None = None
_pickers_lock = threading.Lock()


def pickers() -> Pickers:
    global _pickers
    with _pickers_lock:
        if _pickers is None:
            espn_router = EspnRouter(Breakers(engine=None))
            _pickers = Pickers(
                lambda sport, day: fetch.scoreboard(espn_router, sport, day, WEB_MAX_WAIT).games,
                lambda sport, team: fetch.roster(espn_router, sport, team, WEB_MAX_WAIT))
        return _pickers


# --- How a slip and a leg are shown --------------------------------------------------------------


def leg_json(leg: Leg, tz: ZoneInfo) -> dict:
    event = leg.event
    return {
        "id": leg.id, "summary": fmt.leg_summary(leg), "game": fmt.game_label(leg),
        "pick": fmt.pick_text(leg),
        "event": {"espn_event_id": event.espn_event_id, "sport": event.sport,
                  "start_time": event.start_time, "start_text": fmt.fmt_time(event.start_time, tz),
                  "status": event.status},
        "market_type": leg.market_type, "market_label": fmt.MARKET_LABELS[leg.market_type],
        "side": leg.side, "espn_athlete_id": leg.espn_athlete_id, "player_name": leg.player_name,
        "description": leg.description, "line": leg.line, "american_odds": leg.american_odds,
        "result": leg.result, "final_value": leg.final_value,
        "settlement_source": leg.settlement_source, "needs_review": leg.needs_review,
        "review_reason": leg.review_reason, "tag_ids": sorted(t.id for t in leg.tags),
        # Whether there is one, never what it is: a closing line is an Odds API price.
        "has_closing_line": leg.closing_captured_at is not None,
    }


def slip_json(slip: Slip, tz: ZoneInfo) -> dict:
    return {
        "id": slip.id, "summary": fmt.slip_summary(slip), "logged_by": slip.logged_by,
        "slip_type": slip.slip_type, "american_odds": slip.american_odds,
        "odds_text": fmt.fmt_odds(slip.american_odds), "sportsbook": slip.sportsbook.name,
        "sportsbook_id": slip.sportsbook_id, "is_placed": slip.is_placed, "stake": slip.stake,
        "potential_payout": slip.potential_payout, "boosted": slip.boosted, "status": slip.status,
        "payout": slip.payout,
        "payout_text": None if slip.payout is None else fmt.fmt_money(slip.payout,
                                                                       slip.is_placed),
        "needs_review": slip.needs_review, "review_reason": slip.review_reason,
        "notes": slip.notes, "source": slip.source, "created_at": slip.created_at,
        "created_text": fmt.fmt_time(slip.created_at, tz), "settled_at": slip.settled_at,
        "legs": [leg_json(leg, tz) for leg in slip.legs],
    }


def _load_slip(session: Session, slip_id: int) -> Slip:
    slip = session.scalars(select(Slip).where(Slip.id == slip_id).options(
        selectinload(Slip.legs).selectinload(Leg.event), selectinload(Slip.legs)
        .selectinload(Leg.tags), selectinload(Slip.sportsbook))).one_or_none()
    if slip is None:
        raise fail(404, "No such slip")
    return slip


def _load_leg(session: Session, leg_id: int) -> Leg:
    leg = session.get(Leg, leg_id)
    if leg is None:
        raise fail(404, "No such leg")
    return leg


def _act(session: Session, action) -> None:
    """Run one service call and commit, or answer 400 with what the service said."""
    try:
        action()
    except ValueError as e:  # ServiceError included
        session.rollback()
        raise fail(400, str(e)) from e
    session.commit()
    session.expire_all()  # the answer shows what was stored (amounts as the database keeps them)


# --- The banner, on every page -------------------------------------------------------------------


@router.get("/health")
def health(session: SessionDep, user: UserDep) -> JSONResponse:
    """Who is signed in, what needs review, and the health banner (SPEC.md section 9.2)."""
    now = _utcnow()
    settings = get_settings()
    banners = health_banner(session.scalars(select(SourceHealth)), now,
                            settings.odds_api_reserve, _tz())
    return respond({"user": user, "now": now, "review_count": services.review_count(session, now),
                    "banners": [{"level": b.level, "text": b.text} for b in banners]})


# --- Watch ---------------------------------------------------------------------------------------


def card_json(card: live.Card, tz: ZoneInfo) -> dict:
    provider = card.provider.value if card.provider else "no provider yet"
    return {
        "slip": slip_json(card.slip, tz),
        "legs": [{"id": v.leg_id, "summary": fmt.leg_summary(v.leg), "standing": fmt.standing(v),
                  "status_text": v.status_text, "tracked": v.tracked, "state": v.state,
                  "live_value": v.live_value, "settled": v.settled,
                  "provider": v.provider} for v in card.legs],
        "progress": card.progress, "severity": card.severity,
        "updated_seconds_ago": card.updated_seconds_ago,
        "updated_text": f"updated {fmt.fmt_age(card.updated_seconds_ago)} · via {provider}",
        "no_change_min": card.no_change_min,
        "no_change_text": None if card.no_change_min is None
        else f"No change for {card.no_change_min} min",
    }


@router.get("/live")
def watch(session: SessionDep) -> JSONResponse:
    """Where each pending slip with an NFL game on stands right now (SPEC.md section 9.4). The
    page polls this every 15 s. Nothing here settles anything."""
    now, tz = _utcnow(), _tz()
    cards = live.live_cards(session, now)
    since = live.load_espn_unavailable_since(session)
    return respond({
        "now": now, "refreshed_text": f"Refreshed {fmt.fmt_time(now, tz)}",
        "espn_unavailable_since": since,
        "espn_unavailable_text": None if since is None else fmt.espn_unavailable_text(since, tz),
        "cards": [card_json(c, tz) for c in cards],
        "empty_text": None if cards else fmt.NOTHING_TO_WATCH,
    })


# --- Slips ---------------------------------------------------------------------------------------


@router.get("/slips")
def slips(session: SessionDep,
          view: Literal["recent", "open", "settled"] = "recent",
          limit: Annotated[int, Query(ge=1, le=100)] = 5) -> JSONResponse:
    """`recent`: the latest logged (Log). `open`: placed and pending, so can be cashed out
    (Review). `settled`: settled in the last 7 days, each leg with its verification (Watch)."""
    tz = _tz()
    if view == "recent":
        rows = [slip_json(s, tz) for s in services.recent_slips(session, limit)]
    elif view == "open":
        rows = [slip_json(s, tz) for s in services.open_slips(session)]
    else:
        rows = []
        for settled in live.recent_settled(session, _utcnow()):
            row = slip_json(settled.slip, tz)
            for leg, label in zip(row["legs"], settled.verifications, strict=True):
                leg["verification"] = label.value or None
            rows.append(row)
    return respond({"view": view, "slips": rows,
                    "empty_text": None if rows else {
                        "recent": "Nothing logged yet.", "open": "No open bets.",
                        "settled": "Nothing has settled in the last 7 days."}[view]})


def _drafted_json(drafted) -> dict:
    return {"slip_type": drafted.slip_type, "american_odds": drafted.american_odds,
            "computed_odds": drafted.computed_odds,
            "computed_odds_text": None if drafted.computed_odds is None
            else fmt.fmt_odds(drafted.computed_odds),
            "errors": drafted.errors, "warnings": drafted.warnings}


@router.post("/slips/preview")
def preview_slip(draft: SlipDraft, session: SessionDep) -> JSONResponse:
    """What the form shows as it is filled in: the slip type, a parlay's odds worked out from
    its legs, what is still missing, and the warnings. Writes nothing."""
    drafted = draft_slip(session, draft, pickers(), _utcnow(), save_events=False)
    session.rollback()
    return respond(_drafted_json(drafted))


@router.post("/slips")
def create_slip(draft: SlipDraft, session: SessionDep, user: UserDep) -> JSONResponse:
    """Log a slip, as `user`. 422 with the reasons if anything is missing or wrong."""
    drafted = draft_slip(session, draft, pickers(), _utcnow(), save_events=True)
    if drafted.slip is None:
        session.rollback()
        return respond({"detail": drafted.errors[0], **_drafted_json(drafted)}, status=422)
    try:
        slip = services.create_slip(session, drafted.slip, user)
    except ValueError as e:  # ServiceError: a sportsbook, event or tag that isn't there
        session.rollback()
        return respond({"detail": str(e), **_drafted_json(drafted), "errors": [str(e)]},
                       status=422)
    session.commit()
    session.expire_all()
    return respond({"slip": slip_json(_load_slip(session, slip.id), _tz()),
                    "warnings": drafted.warnings}, status=201)


class Amount(BaseModel):
    amount: Decimal = Field(ge=0, le=100_000_000, decimal_places=2)


@router.post("/slips/{slip_id}/payout")
def slip_payout(slip_id: int, body: Amount, session: SessionDep) -> JSONResponse:
    """What the sportsbook actually paid, e.g. for a reduced same-game parlay."""
    slip = _load_slip(session, slip_id)
    _act(session, lambda: services.enter_slip_payout(session, slip, body.amount))
    return respond({"slip": slip_json(_load_slip(session, slip_id), _tz())})


@router.post("/slips/{slip_id}/cash-out")
def slip_cash_out(slip_id: int, body: Amount, session: SessionDep) -> JSONResponse:
    """Cashed out early: the slip ends at the amount; its legs still settle for analytics."""
    slip = _load_slip(session, slip_id)
    _act(session, lambda: services.mark_cashed_out(session, slip, body.amount))
    return respond({"slip": slip_json(_load_slip(session, slip_id), _tz())})


# --- Legs ----------------------------------------------------------------------------------------


class LegChange(BaseModel):
    """`settle` by hand: the final stat (preferred: the result is worked out) or the result.
    `reopen`: undo a settlement. `tags`: replace the leg's tags."""
    action: Literal["settle", "reopen", "tags"]
    result: Literal["win", "loss", "push", "void"] | None = None
    final_value: Decimal | None = Field(default=None, ge=-9999, le=9999)
    tag_ids: list[int] = Field(default=[], max_length=20)


@router.patch("/legs/{leg_id}")
def change_leg(leg_id: int, body: LegChange, session: SessionDep) -> JSONResponse:
    leg = _load_leg(session, leg_id)
    if body.action == "settle":
        result = None if body.result is None else LegResult(body.result)
        _act(session, lambda: services.settle_leg_manually(session, leg, result=result,
                                                           final_value=body.final_value))
    elif body.action == "reopen":
        _act(session, lambda: services.reopen_leg(session, leg))
    else:
        _act(session, lambda: services.set_leg_tags(session, leg, body.tag_ids))
    return respond({"slip": slip_json(_load_slip(session, leg.slip_id), _tz())})


class ClosingLine(BaseModel):
    closing_line: Decimal = Field(ge=-999, le=9999)
    closing_odds: int
    closing_opposite_odds: int | None = None  # the Under, or the other team, at that line


@router.post("/legs/{leg_id}/closing")
def closing_line(leg_id: int, body: ClosingLine, session: SessionDep) -> JSONResponse:
    """A closing line entered by hand, when the worker couldn't capture one."""
    leg = _load_leg(session, leg_id)
    _act(session, lambda: services.set_closing_line(
        session, leg, closing_line=body.closing_line, closing_odds=body.closing_odds,
        closing_opposite_odds=body.closing_opposite_odds))
    return respond({"slip": slip_json(_load_slip(session, leg.slip_id), _tz())})


# --- Review --------------------------------------------------------------------------------------


@router.get("/review")
def review(session: SessionDep) -> JSONResponse:
    """Everything a person has to settle or fill in, most urgent kind first (SPEC.md 9.5). Each
    item is acted on through the slip and leg routes above."""
    now, tz = _utcnow(), _tz()
    items = services.review_queue(session, now)
    return respond({
        "items": [{"kind": item.kind, "title": KIND_TITLES[item.kind], "reason": item.reason,
                   "slip": slip_json(item.slip, tz),
                   "leg_id": None if item.leg is None else item.leg.id} for item in items],
        "count": sum(1 for item in items if item.kind != "closing_line"),
        "empty_text": None if items else "Nothing to review.",
    })


# --- Analytics -----------------------------------------------------------------------------------


def _selection_row(s: an.SelectionStats) -> dict:
    return {
        "label": s.label, "n": s.n,
        "price_clv": fmt.clv_text(s.price_clv, "pp", 2),
        "line_clv": fmt.clv_text(s.line_clv, "pts", 1),
        "hit_rate": fmt.hit_rate_text(s),
        "break_even": fmt.pct(s.break_even) + (f" (n {s.n_odds})" if s.n_odds != s.n else ""),
        "edge": fmt.signed(s.edge, "pp"),
        "roi": fmt.DASH if s.roi is None else f"{s.roi * 100:+.1f}%",
        "evidence": fmt.evidence_text(s), "pushes": s.pushes, "low_sample": s.low_sample,
    }


def _money_row(m: an.MoneyStats, units: bool) -> dict:
    return {"label": m.label, "n": m.n, "staked": f"{m.staked:.2f}",
            "returned": f"{m.returned:.2f}",
            "profit": f"{m.profit:+.2f}" if units else f"{m.profit:.2f}",
            "roi": fmt.DASH if m.roi is None else f"{m.roi * 100:+.1f}%",
            "low_sample": m.low_sample}


def _filters(filters: list[str], tags: list[int], match: str) -> an.Filters:
    values: dict[str, set[str]] = {}
    for item in filters:
        dim, _, bucket = item.partition(":")
        if dim not in an.LEG_DIMENSIONS or not bucket:
            raise fail(422, f"Unknown filter {item!r}: use dimension:value")
        values.setdefault(dim, set()).add(bucket)
    return an.Filters({d: frozenset(v) for d, v in values.items()}, frozenset(tags), match)


@router.get("/analytics")
def analytics(session: SessionDep,
              group: Annotated[str, Query(max_length=20)] = "sport",
              money_group: Annotated[str, Query(max_length=20)] = "slip_type",
              filter: Annotated[list[str], Query(max_length=40)] = [],  # noqa: A002, B006
              tag: Annotated[list[int], Query(max_length=40)] = [],  # noqa: B006
              match: Literal["any", "all"] = "any") -> JSONResponse:
    """Where is the edge, and what did the money do? (SPEC.md section 10.) Selections pool both
    users, per leg: `group` is a dimension or `all`, `filter` is `dimension:value` (repeat it),
    `tag` a tag id (repeat it, with `match`). Money is per slip, by `money_group`. Every figure
    carries its n, and a group under MIN_SAMPLE decided legs is marked low sample."""
    if group != "all" and group not in an.LEG_DIMENSIONS:
        raise fail(422, f"Unknown group {group!r}")
    if money_group not in an.SLIP_DIMENSIONS:
        raise fail(422, f"Unknown money group {money_group!r}")
    min_sample = get_settings().min_sample
    leg_rows, slip_rows = an.load_leg_rows(session), an.load_slip_rows(session)
    tags = list(session.scalars(select(Tag).order_by(Tag.category, Tag.name)))
    chosen = _filters(filter, tag, match)
    rows = an.apply_filters(leg_rows, chosen)
    (overall,) = an.group_selections(rows, None, min_sample)
    dimension = None if group == "all" else group
    placed = an.group_slips(slip_rows, money_group, placed=True, min_sample=min_sample)
    unplaced = an.group_slips(slip_rows, money_group, placed=False, min_sample=min_sample)
    return respond({
        "empty_text": None if slip_rows else ("Nothing logged yet. Log a bet, and this page "
                                              "fills in as games finish."),
        "min_sample": min_sample,
        "dimensions": {k: v[0] for k, v in an.LEG_DIMENSIONS.items()},
        "money_dimensions": {k: v[0] for k, v in an.SLIP_DIMENSIONS.items()},
        "filter_options": an.filter_options(leg_rows),
        "tags": [{"id": t.id, "label": f"{t.category}: {t.name}"} for t in tags],
        "selections": {
            "caution": an.caution(chosen),
            "price_clv": {"text": fmt.signed(overall.price_clv.value, "pp", 2),
                          "n": overall.price_clv.n},
            "line_clv": {"text": fmt.signed(overall.line_clv.value, "pts"),
                         "n": overall.line_clv.n},
            "group": group,
            "group_label": "Group" if dimension is None else an.LEG_DIMENSIONS[dimension][0],
            "rows": [] if not overall.n else [
                _selection_row(s) for s in an.group_selections(rows, dimension, min_sample)],
            "empty_text": None if overall.n else "No settled legs match yet.",
        },
        "money": {
            "group": money_group, "group_label": an.SLIP_DIMENSIONS[money_group][0],
            "placed": [_money_row(m, False) for m in placed] if any(m.n for m in placed)
            else [],
            "placed_empty_text": None if any(m.n for m in placed)
            else "No placed slip has settled yet.",
            "unplaced": [_money_row(m, True) for m in unplaced] if any(m.n for m in unplaced)
            else [],
            "unplaced_empty_text": None if any(m.n for m in unplaced)
            else "No unplaced slip has settled yet.",
            "cumulative_profit": [{"settled_at": t, "profit": p}
                                  for t, p in an.cumulative_profit(slip_rows, placed=True)],
        },
    })


# --- The Log form's pickers ----------------------------------------------------------------------


def _team(team: espn.Team) -> dict:
    return {"espn_id": team.espn_id, "abbreviation": team.abbreviation, "name": team.name}


@router.get("/games")
def games(sport: Sport = Sport.NFL, day: Annotated[date | None, Query(alias="date")] = None
          ) -> JSONResponse:
    """A game day's games (US Eastern; today by default), and the markets of the sport."""
    day = day or espn.today_game_day()
    tz = _tz()
    found, problem = pickers().games(sport, day)
    return respond({
        "sport": sport, "date": day, "problem": problem,
        "games": [{"espn_event_id": g.espn_event_id, "label": game_label(g, tz),
                   "start_time": g.start_time, "status": g.status, "home": _team(g.home),
                   "away": _team(g.away)} for g in found],
        "markets": [{"key": m, "label": fmt.MARKET_LABELS[m]} for m in markets_for(sport)],
    })


@router.get("/roster/{event_id}")
def roster(event_id: Annotated[str, Path(pattern=r"^\d{1,20}$")], sport: Sport = Sport.NFL,
           day: Annotated[date | None, Query(alias="date")] = None) -> JSONResponse:
    """Both teams' players in a game, away team first, for the player picker."""
    game, problem = pickers().game(sport, day or espn.today_game_day(), event_id)
    if game is None:
        raise fail(404, problem or "That game isn't on that day's scoreboard")
    players, problem = pickers().players(sport, game)
    return respond({"problem": problem,
                    "players": [{"espn_athlete_id": a, "name": name, "label": label}
                                for a, (name, label) in players.items()]})


# --- Settings: tags and sportsbooks ---------------------------------------------------------------


def _tag_json(tag: Tag) -> dict:
    return {"id": tag.id, "category": tag.category, "name": tag.name, "retired": tag.retired,
            "label": f"{tag.category}: {tag.name}" + (" (retired)" if tag.retired else "")}


@router.get("/settings/tags")
def tags(session: SessionDep) -> JSONResponse:
    """Every tag for Settings; `choices` is the active ones, most used first, for the form."""
    return respond({"tags": [_tag_json(t) for t in services.all_tags(session)],
                    "choices": [_tag_json(t) for t in services.tag_choices(session)]})


class TagIn(BaseModel):
    category: str = Field(max_length=50)
    name: str = Field(max_length=100)


@router.post("/settings/tags")
def add_tag(body: TagIn, session: SessionDep) -> JSONResponse:
    holder: list[Tag] = []
    _act(session, lambda: holder.append(services.create_tag(session, body.category, body.name)))
    return respond({"tag": _tag_json(holder[0])}, status=201)


class TagChange(BaseModel):
    category: str | None = Field(default=None, max_length=50)
    name: str | None = Field(default=None, max_length=100)
    retired: bool | None = None


@router.patch("/settings/tags/{tag_id}")
def change_tag(tag_id: int, body: TagChange, session: SessionDep) -> JSONResponse:
    """Rename (category and name together), retire or restore a tag."""
    tag = session.get(Tag, tag_id)
    if tag is None:
        raise fail(404, "No such tag")

    def change() -> None:
        if body.category is not None or body.name is not None:
            services.rename_tag(session, tag, tag.category if body.category is None
                                else body.category, tag.name if body.name is None else body.name)
        if body.retired is not None:
            services.set_tag_retired(session, tag, body.retired)
    _act(session, change)
    return respond({"tag": _tag_json(tag)})


class Merge(BaseModel):
    into: int


@router.post("/settings/tags/{tag_id}/merge")
def merge_tag(tag_id: int, body: Merge, session: SessionDep) -> JSONResponse:
    """Move every leg from this tag to `into`, then delete this one."""
    source, target = session.get(Tag, tag_id), session.get(Tag, body.into)
    if source is None or target is None:
        raise fail(404, "No such tag")
    _act(session, lambda: services.merge_tags(session, source, target))
    return respond({"tag": _tag_json(target)})


def _book_json(book: Sportsbook) -> dict:
    return {"id": book.id, "name": book.name, "odds_api_key": book.odds_api_key}


@router.get("/settings/sportsbooks")
def books(session: SessionDep) -> JSONResponse:
    return respond({"sportsbooks": [_book_json(b) for b in services.sportsbooks(session)]})


class BookIn(BaseModel):
    name: str = Field(max_length=50)
    # The Odds API's bookmaker key (e.g. draftkings), which links a book to its closing lines.
    odds_api_key: str | None = Field(default=None, max_length=50)


@router.post("/settings/sportsbooks")
def add_book(body: BookIn, session: SessionDep) -> JSONResponse:
    holder: list[Sportsbook] = []
    _act(session, lambda: holder.append(
        services.create_sportsbook(session, body.name, body.odds_api_key)))
    return respond({"sportsbook": _book_json(holder[0])}, status=201)


@router.patch("/settings/sportsbooks/{book_id}")
def change_book(book_id: int, body: BookIn, session: SessionDep) -> JSONResponse:
    book = session.get(Sportsbook, book_id)
    if book is None:
        raise fail(404, "No such sportsbook")
    _act(session, lambda: services.update_sportsbook(session, book, body.name, body.odds_api_key))
    return respond({"sportsbook": _book_json(book)})

