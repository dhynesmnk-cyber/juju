"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ParlayTray from "@/components/ParlayTray";
import ResultCard from "@/components/ResultCard";
import WorldStage from "@/components/world/WorldStage";
import { MOOD, worldFor } from "@/components/world/worlds";
import { quarter, secondsAgo } from "@/lib/format";
import { gameHref } from "@/lib/links";
import { legKey } from "@/lib/parlayTray";
import type { Card, PlayerView, TeamView } from "@/lib/types";

const SETTLED = new Set(["won", "lost", "push", "void", "no_stat_line"]);

type View = PlayerView | TeamView;

function title(v: View): { name: string; sub: string } {
  if ("player" in v) {
    const bits = [v.player.team_abbr, v.player.position].filter(Boolean).join(" · ");
    return { name: v.player.name, sub: bits };
  }
  return { name: `${v.team.name}`, sub: "Game and team lines" };
}

function isDone(v: View): boolean {
  return v.game.status === "final" && v.cards.every((c) => SETTLED.has(c.outcome) || c.price === null);
}

/** The shared card (`?card=`) first, then the rest in the backend's order; bets with no price
 *  go last, where they are listed in a line each rather than as cards. */
function pinned(v: View, key?: string): View {
  const priced = v.cards.filter((c) => c.price !== null);
  const cards = [...priced, ...v.cards.filter((c) => c.price === null)];
  const i = key ? cards.findIndex((c) => c.key === key) : -1;
  if (i <= 0) return { ...v, cards } as View;
  return { ...v, cards: [cards[i], ...cards.slice(0, i), ...cards.slice(i + 1)] } as View;
}

const NO_PRICE = /^No price on file: /;

/** A card's own link: this page with the card first (and its line, for a ladder). */
export function sharePath(pagePath: string, card: Card): string {
  const q = new URLSearchParams({ card: card.key });
  if (card.alternate && card.line) q.set("threshold", card.line);
  return `${pagePath}?${q}`;
}

/** The card as a parlay leg, for the tray; undefined for a card that can't be one. */
function parlayLeg(v: View, c: Card) {
  const key = "player" in v ? legKey("player", v.player.id, c)
    : legKey("team", v.team.espn_id, c);
  if (!key || c.outcome === "void") return undefined;
  const who = "player" in v ? v.player.name : v.team.abbr ?? v.team.name;
  return { game: v.game.id, leg: { key, who, bet: c.bet } };
}

/** Shows the price straight away and keeps the status current: every 10 s until settled. */
export default function ResultView({ initial: fromServer, apiPath, pagePath, pin }: {
  initial: View; apiPath: string; pagePath: string; pin?: string;
}) {
  const initial = useMemo(() => pinned(fromServer, pin), [fromServer, pin]);
  const [view, setView] = useState<View>(initial);
  const [changed, setChanged] = useState<Set<string>>(new Set());
  const [offline, setOffline] = useState(false);
  const [announce, setAnnounce] = useState("");
  const previous = useRef<Map<string, Card["outcome"]>>(
    new Map(initial.cards.map((c) => [c.key, c.outcome])));
  const first = view.cards[0];
  // A card already cashed when the page opens gets its shockwave, but shows its number as is:
  // the count-up is for a bet that cashes while you watch (counting from $10 on load made the
  // number drop and climb after it had been read).
  const [shockAt, setShockAt] = useState<number | null>(null);
  const [countFirst, setCountFirst] = useState(false);
  useEffect(() => {
    if (initial.cards[0]?.outcome !== "locked"
        || ("waiting_for_feed" in initial && initial.waiting_for_feed)) return;
    const id = window.setTimeout(() => setShockAt(Date.now()), 450);
    return () => window.clearTimeout(id);
  }, [initial]);
  const shockFrom = useCallback(
    () => document.getElementById("first-card")?.getBoundingClientRect() ?? null, []);
  const waitingNow = "waiting_for_feed" in view && view.waiting_for_feed;

  useEffect(() => {
    if (isDone(view)) return;
    const t = setInterval(async () => {
      try {
        const r = await fetch(apiPath);
        if (!r.ok) throw new Error();
        const next: View = pinned(await r.json(), pin);
        const moved = new Set(next.cards.filter(
          (c) => previous.current.get(c.key) !== c.outcome).map((c) => c.key));
        const top = next.cards[0];
        if (top && moved.has(top.key)) {
          setAnnounce(`${top.label}: ${top.outcome_text}. ${top.headline}`);
          if (top.outcome === "locked" || top.outcome === "won") setCountFirst(true);
          if (top.outcome === "locked") setShockAt(Date.now());
        }
        previous.current = new Map(next.cards.map((c) => [c.key, c.outcome]));
        setChanged(moved);
        setView(next);
        setOffline(false);
      } catch {
        setOffline(true);
      }
    }, waitingNow ? 5_000 : 10_000);  // faster while the feed is catching up
    return () => clearInterval(t);
  }, [apiPath, view, waitingNow, pin]);

  const { name, sub } = title(view);
  const priced = view.cards.filter((c) => c.price !== null);
  const unpriced = view.cards.filter((c) => c.price === null);
  const g = view.game;
  const score = g.home.score !== null && g.away.score !== null
    ? `${g.away.abbr ?? g.away.name} ${g.away.score} – ${g.home.score} ${g.home.abbr ?? g.home.name}`
    : g.label;
  const waiting = waitingNow;
  // "We're looking for your play" wins over the first card: that is the story on screen.
  const world = waiting ? "wait" : worldFor(first?.outcome, !!first?.price);
  return (
    <>
      <WorldStage world={world} shockAt={shockAt} shockFrom={shockFrom} />
      <div className="sr-only" aria-live="polite">{announce}</div>
      <div className="result-head">
        <Link className="back" href={gameHref(g.id)}>‹ {g.away.abbr ?? g.away.name} @ {g.home.abbr ?? g.home.name}, every play</Link>
        <h1>{name}</h1>
        <div className="sub">{[sub, score, g.status_text].filter(Boolean).join(" · ")}</div>
        {g.status !== "scheduled" && g.status !== "final" && (
          <div className={`fresh ${g.freshness}`}>
            Live stats {secondsAgo(g.updated_seconds_ago)}
            {g.freshness === "red" ? ". The live feed looks stuck." : ""}
          </div>
        )}
        {offline && <div className="fresh red" role="alert">Can&apos;t refresh right now. Retrying.</div>}
      </div>
      {view.the_play && (
        <p className="the-play" role="note">
          <span className="muted small">The play </span>
          {view.the_play.label}
          {view.the_play.period
            ? <span className="muted small"> · {quarter(view.the_play.period)} {view.the_play.clock ?? ""}</span>
            : null}
        </p>
      )}
      {waiting && (
        <div className="waiting" role="status">
          That play isn&apos;t in the official feed yet. It usually shows up within a minute:
          this page updates by itself. Prices below are already final.
        </div>
      )}
      <div className="cards">
        {view.cards.length === 0 && (
          <p className="muted">
            {("no_price" in view && view.no_price)
              || `No archived prices for ${name} in this game. Juju only has prices captured before kickoff.`}
          </p>
        )}
        {priced.map((c, i) => (
          <ResultCard key={c.key} card={c} changed={changed.has(c.key)}
                      id={i === 0 ? "first-card" : undefined}
                      crew={i === 0 ? MOOD[world] : undefined}
                      countUp={i === 0 && countFirst} sharePath={sharePath(pagePath, c)}
                      parlay={parlayLeg(view, c)} />
        ))}
        {unpriced.length > 0 && (
          <section className="unpriced" aria-label="Bets with no price">
            <h2>No price on file</h2>
            <ul>
              {unpriced.map((c) => (
                <li key={c.key}><strong>{c.label}</strong>: {(c.no_price ?? "").replace(NO_PRICE, "")}</li>
              ))}
            </ul>
          </section>
        )}
      </div>
      <ParlayTray game={view.game.id} />
      <p><Link className="back" href="/">← Look up another play</Link></p>
    </>
  );
}
