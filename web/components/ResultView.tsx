"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import ResultCard from "@/components/ResultCard";
import WorldStage from "@/components/world/WorldStage";
import { MOOD, worldFor } from "@/components/world/worlds";
import { secondsAgo } from "@/lib/format";
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

/** Shows the price straight away and keeps the status current: every 10 s until settled. */
export default function ResultView({ initial, apiPath }: { initial: View; apiPath: string }) {
  const [view, setView] = useState<View>(initial);
  const [changed, setChanged] = useState<Set<string>>(new Set());
  const [offline, setOffline] = useState(false);
  const [announce, setAnnounce] = useState("");
  const previous = useRef<Map<string, Card["outcome"]>>(
    new Map(initial.cards.map((c) => [c.key, c.outcome])));
  const first = view.cards[0];
  // A card that is already cashed when the page opens gets its moment too.
  const [shockAt, setShockAt] = useState<number | null>(null);
  const [countFirst, setCountFirst] = useState(
    () => initial.cards[0]?.outcome === "locked" || initial.cards[0]?.outcome === "won");
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
        const next: View = await r.json();
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
  }, [apiPath, view, waitingNow]);

  const { name, sub } = title(view);
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
      {waiting && (
        <div className="waiting" role="status">
          That play isn&apos;t in the official feed yet. It usually shows up within a minute:
          this page updates by itself. Prices below are already final.
        </div>
      )}
      <div aria-live="polite">
        {view.cards.length === 0 && (
          <p className="muted">
            No archived prices for {name} in this game. Juju only has prices captured before
            kickoff.
          </p>
        )}
        {view.cards.map((c, i) => (
          <ResultCard key={c.key} card={c} changed={changed.has(c.key)}
                      id={i === 0 ? "first-card" : undefined}
                      crew={i === 0 ? MOOD[world] : undefined}
                      countUp={i === 0 && countFirst} />
        ))}
      </div>
      <p className="hint hypo">{view.disclaimer}</p>
      <p><Link href="/">← Look up another play</Link></p>
    </>
  );
}
