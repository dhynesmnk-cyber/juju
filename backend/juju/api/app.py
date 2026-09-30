"""The internal HTTP API. It is reached only through the Next.js site over Fly's private network
and has no public address (docs/licensing.md).

Every endpoint reads Postgres; none calls a data provider. The optional LLM fallback in
`/api/lookup` is the one outbound call.
"""
import logging
import threading
import time
from collections import defaultdict, deque
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from juju.api import lookup
from juju.api.views import Focus, game_view, player_view, team_view
from juju.config import Settings, get_settings
from juju.core.enums import EventStatus, HealthState, PlayKind
from juju.core.models import Game, HotGame, Lookup, OddsSnapshot, Play, SourceHealth
from juju.db import session_factory
from juju.ingest.llm import Budget, LlmReader

log = logging.getLogger("juju.api")

app = FastAPI(title="Juju internal API", docs_url=None, redoc_url=None, openapi_url=None)

LOOKUPS_PER_MINUTE = 30
# Past this many lookups in the window, a client is asked to pass Cloudflare Turnstile, when
# the site has it configured (it says so with `x-juju-challenge`, and vouches for a person who
# passed with `x-juju-verified`). The backend is private, so only the site sets these headers.
CHALLENGE_AFTER = 12
CHALLENGE_WINDOW_SECONDS = 15 * 60
CHALLENGE_TEXT = "Quick check that you're a person, then Juju will look it up."
LIVE_CACHE = "public, s-maxage=5, stale-while-revalidate=30"
FINAL_CACHE = "public, s-maxage=300, stale-while-revalidate=3600"
DISCLAIMER = ("Hypothetical: what a $10 bet at the archived price would have returned. "
              "Juju is not a sportsbook and does not take bets.")


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def get_session() -> Iterator[Session]:
    with session_factory()() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def respond(data, cache: str | None = None) -> JSONResponse:
    body = jsonable_encoder(data, custom_encoder={Decimal: lambda d: format(d, "f")})
    headers = {"Cache-Control": cache or "no-store"}
    return JSONResponse(body, headers=headers)


# --- Abuse control ---------------------------------------------------------------------------


class RateLimit:
    """Per client, a sliding window (a minute by default). In-process: fine for the one or two
    API machines; the edge rules at Cloudflare are the first line (docs/deploy.md)."""

    def __init__(self, per_minute: int, window_seconds: float = 60):
        self.per_minute = per_minute
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, client: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[client]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.per_minute:
                return False
            hits.append(now)
            return True


_limit = RateLimit(LOOKUPS_PER_MINUTE)
_challenge = RateLimit(CHALLENGE_AFTER, CHALLENGE_WINDOW_SECONDS)
_llm: LlmReader | None = None
_llm_lock = threading.Lock()


def llm_reader(settings: Settings) -> LlmReader | None:
    global _llm
    if not settings.llm_enabled:
        return None
    with _llm_lock:
        if _llm is None:
            assert settings.llm_api_key is not None and settings.llm_model
            _llm = LlmReader(settings.llm_api_key.get_secret_value(), settings.llm_base_url,
                             settings.llm_model, Budget(settings.llm_daily_budget))
        return _llm


def client_id(request: Request) -> str:
    # Set by the Next.js site from Cloudflare's CF-Connecting-IP; used for rate limits only,
    # never stored.
    return request.headers.get("x-juju-client") or (
        request.client.host if request.client else "unknown")


_hot_written: dict[int, float] = {}


def mark_hot(session: Session, game_id: int, now: datetime) -> None:
    """Tell the live loop people are looking at this game (at most every 10 s per game)."""
    last = _hot_written.get(game_id)
    if last is not None and time.monotonic() - last < 10:
        return
    _hot_written[game_id] = time.monotonic()
    session.execute(insert(HotGame).values(game_id=game_id, last_lookup_at=now)
                    .on_conflict_do_update(index_elements=[HotGame.game_id],
                                           set_={"last_lookup_at": now}))
    session.commit()


# --- Endpoints --------------------------------------------------------------------------------


@app.get("/health")
def health(session: SessionDep) -> JSONResponse:
    now = _utcnow()
    rows = {r.source: r for r in session.scalars(select(SourceHealth))}
    worker = rows.get("worker")
    beat = worker.last_success_at if worker else None
    upcoming = session.scalars(select(Game).where(
        Game.commence_time.between(now, now + timedelta(hours=6)))
        .order_by(Game.commence_time)).all()
    captures = []
    for g in upcoming:
        slots = sorted(session.scalars(select(OddsSnapshot.slot).where(
            OddsSnapshot.game_id == g.id)))
        captures.append({"game": g.label, "kickoff": g.commence_time, "slots": slots})
    worker_ok = beat is not None and now - beat < timedelta(minutes=3)
    body = {
        "ok": worker_ok,
        "worker_heartbeat_seconds_ago": None if beat is None else int((now - beat).total_seconds()),
        "providers": {s: {"state": r.state, "failure": r.failure_kind, "open_until": r.open_until,
                          "last_error": r.last_error} for s, r in rows.items()
                      if s not in ("worker",)},
        "odds_api_quota_remaining": rows["odds_api"].quota_remaining if "odds_api" in rows
        else None,
        "next_captures": captures,
    }
    return respond(body)


@app.get("/api/status")
def status(session: SessionDep) -> JSONResponse:
    """What the site banner needs: is live data degraded right now?"""
    rows = list(session.scalars(select(SourceHealth)))
    espn = [r for r in rows if r.source.startswith("espn_")]
    espn_down = bool(espn) and all(r.state is HealthState.OPEN for r in espn)
    worker = next((r for r in rows if r.source == "worker"), None)
    stale_worker = worker is None or worker.last_success_at is None or (
        _utcnow() - worker.last_success_at > timedelta(minutes=3))
    message = None
    if stale_worker:
        message = "Live updates are paused. Archived prices are still shown."
    elif espn_down:
        message = "Live stats are unavailable right now. Archived prices are still shown."
    return respond({"degraded": message is not None, "message": message},
                   "public, s-maxage=10")


def _plays_for(session: Session, game: Game, limit: int = 4) -> list[dict]:
    rows = session.scalars(select(Play).where(Play.game_id == game.id)
                           .order_by(Play.period.desc(), Play.sequence.desc()).limit(12)).all()
    out = []
    for p in rows:
        if p.espn_athlete_id is None and not (p.scoring and p.team_espn_id):
            continue
        out.append({"id": p.id, "kind": p.kind, "text": p.text, "yards": p.yards,
                    "period": p.period, "clock": p.clock, "wallclock": p.wallclock,
                    "athlete_id": p.espn_athlete_id, "team_espn_id": p.team_espn_id,
                    "scoring": p.scoring, "label": p.label})
        if len(out) >= limit:
            break
    return out


@app.get("/api/live")
def live(session: SessionDep) -> JSONResponse:
    """Games in play, then upcoming, then just finished, with their latest notable plays. No
    prices here."""
    now = _utcnow()
    games = session.scalars(select(Game).where(
        Game.commence_time.between(now - timedelta(hours=14), now + timedelta(hours=36)))
        .order_by(Game.commence_time)).all()
    rank = {EventStatus.IN_PROGRESS: 0, EventStatus.BREAK: 0, EventStatus.DELAYED: 0,
            EventStatus.SCHEDULED: 1, EventStatus.FINAL: 2}
    games = sorted(games, key=lambda g: (rank.get(g.status, 3), g.commence_time))
    out = [{**jsonable_encoder(game_view(g, now)), "plays": _plays_for(session, g)}
           for g in games]
    upcoming = [g for g in games if g.status is EventStatus.SCHEDULED and g.commence_time > now]
    nxt = min(upcoming, key=lambda g: g.commence_time) if upcoming else None
    return respond({"games": out,
                    "next_kickoff": None if nxt is None else {
                        "label": nxt.label, "commence_time": nxt.commence_time}},
                   "public, s-maxage=5, stale-while-revalidate=15")


@app.get("/api/suggest")
def suggest(session: SessionDep, q: Annotated[str, Query(max_length=60)] = "") -> JSONResponse:
    return respond({"choices": lookup.suggest(session, q, _utcnow())},
                   "public, s-maxage=30")


class LookupIn(BaseModel):
    text: str = Field(min_length=1, max_length=200)


@app.post("/api/lookup")
def do_lookup(body: LookupIn, request: Request, session: SessionDep) -> JSONResponse:
    client = client_id(request)
    if not _limit.allow(client):
        raise HTTPException(429, "Too many lookups. Try again in a minute.")
    if (request.headers.get("x-juju-challenge") == "turnstile"
            and request.headers.get("x-juju-verified") != "1"
            and not _challenge.allow(client)):
        return JSONResponse({"detail": CHALLENGE_TEXT, "challenge": "turnstile"}, 428,
                            headers={"Cache-Control": "no-store"})
    started = time.monotonic()
    settings = get_settings()
    result = lookup.resolve(session, body.text, _utcnow(), llm_reader(settings))
    ms = int((time.monotonic() - started) * 1000)
    p = result.parsed
    session.add(Lookup(path=result.path, text=body.text[:200], milliseconds=ms,
                       result=f"{result.kind}:{result.id or ''}"[:80]))
    session.commit()
    focus = {"play": p.play, "market": p.market_key, "threshold": p.threshold,
             "expect": result.expect, "yards": p.yards}
    return respond({"kind": result.kind, "path": result.path, "game_id": result.game_id,
                    "id": result.id, "choices": result.choices, "message": result.message,
                    "focus": focus})


def _focus(play: str | None, market: str | None, threshold: str | None,
           expect: bool, yards: int | None = None) -> Focus:
    try:
        kind = PlayKind(play) if play else None
    except ValueError:
        kind = None
    try:
        t = Decimal(threshold) if threshold else None
        if t is not None and (t < 0 or t > 999 or (t * 2) % 1 != 0):
            t = None
    except ArithmeticError:
        t = None
    if yards is not None and not 0 < yards < 110:
        yards = None
    return Focus(kind, market, t, expect, yards)


def _cache_for(status: EventStatus) -> str:
    return FINAL_CACHE if status is EventStatus.FINAL else LIVE_CACHE


@app.get("/api/player/{game_id}/{athlete_id}")
def player(game_id: int, athlete_id: str, session: SessionDep, response: Response,
           play: str | None = None, market: str | None = None, threshold: str | None = None,
           expect: bool = False, yards: int | None = None) -> JSONResponse:
    game = session.get(Game, game_id)
    if game is None:
        raise HTTPException(404, "No such game")
    now = _utcnow()
    view = player_view(session, game, athlete_id, now, get_settings().books,
                       _focus(play, market, threshold, expect, yards))
    if view is None:
        raise HTTPException(404, "No such player")
    mark_hot(session, game.id, now)
    return respond({**jsonable_encoder(view, custom_encoder={Decimal: lambda d: format(d, "f")}),
                    "disclaimer": DISCLAIMER}, _cache_for(game.status))


@app.get("/api/team/{game_id}/{team_id}")
def team(game_id: int, team_id: str, session: SessionDep, play: str | None = None,
         market: str | None = None) -> JSONResponse:
    game = session.get(Game, game_id)
    if game is None:
        raise HTTPException(404, "No such game")
    now = _utcnow()
    view = team_view(session, game, team_id, now, get_settings().books,
                     _focus(play, market, None, False))
    if view is None:
        raise HTTPException(404, "No such team in this game")
    mark_hot(session, game.id, now)
    return respond({**jsonable_encoder(view, custom_encoder={Decimal: lambda d: format(d, "f")}),
                    "disclaimer": DISCLAIMER}, _cache_for(game.status))
