"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import Payout, { payoutText } from "@/components/Payout";
import RoundRobin from "@/components/RoundRobin";
import ShareButton from "@/components/ShareButton";
import StatusChip, { CASHED, TONE } from "@/components/StatusChip";
import WorldStage from "@/components/world/WorldStage";
import { worldFor } from "@/components/world/worlds";
import { american } from "@/lib/format";
import { gameHref } from "@/lib/links";
import { legCardHref } from "@/lib/parlayTray";
import type { Outcome, ParlayLeg, ParlayView as View } from "@/lib/types";

const SETTLED = new Set<Outcome>(["won", "lost", "push", "void", "no_stat_line"]);

/** Each leg is a light: lit once it has cashed, out once it has lost. */
function light(o: Outcome): string {
  if (o === "locked" || o === "won") return "lit";
  if (o === "lost" || o === "gone") return "out";
  if (o === "push" || o === "void") return "dropped";
  return "waiting";
}

function progress(leg: ParlayLeg): string | null {
  if (leg.current === null) return null;
  if (leg.outcome === "live" && leg.needed !== null) {
    return `${Number(leg.current)} so far, needs ${Number(leg.needed)} more`;
  }
  return `${Number(leg.current)} so far`;
}

/** The parlay as a ticket: the price at once, the status every 10 s until it settles. The gold
 *  shockwave fires only when every leg is lit. `sharePath`: this parlay's own address. */
export default function ParlayView({ initial, apiPath, sharePath }: {
  initial: View; apiPath: string; sharePath: string;
}) {
  const [view, setView] = useState<View>(initial);
  const [offline, setOffline] = useState(false);
  const [shockAt, setShockAt] = useState<number | null>(null);
  const [announce, setAnnounce] = useState("");
  const previous = useRef<Outcome>(initial.outcome);
  const shockFrom = useCallback(
    () => document.getElementById("parlay-card")?.getBoundingClientRect() ?? null, []);

  useEffect(() => {
    if (initial.outcome !== "locked") return;
    const id = window.setTimeout(() => setShockAt(Date.now()), 450);
    return () => window.clearTimeout(id);
  }, [initial]);

  useEffect(() => {
    if (view.game.status === "final" && SETTLED.has(view.outcome)) return;
    const t = setInterval(async () => {
      try {
        const r = await fetch(apiPath);
        if (!r.ok) throw new Error();
        const next: View = await r.json();
        if (next.outcome !== previous.current) {
          setAnnounce(`Parlay: ${next.outcome_text}. ${payoutText(next)}`);
          if (next.outcome === "locked") setShockAt(Date.now());
        }
        previous.current = next.outcome;
        setView(next);
        setOffline(false);
      } catch {
        setOffline(true);
      }
    }, 10_000);
    return () => clearInterval(t);
  }, [apiPath, view]);

  const g = view.game;
  const score = g.home.score !== null && g.away.score !== null
    ? `${g.away.abbr ?? g.away.name} ${g.away.score} – ${g.home.score} ${g.home.abbr ?? g.home.name}`
    : g.label;
  const cashed = CASHED.has(view.outcome);
  const lit = view.legs.filter((l) => light(l.outcome) === "lit").length;
  return (
    <>
      <WorldStage world={worldFor(view.outcome, view.returns !== null)} shockAt={shockAt}
                  shockFrom={shockFrom} />
      <div className="sr-only" aria-live="polite">{announce}</div>
      <div className="result-head">
        <Link className="pill" href={gameHref(g.id)}>
          <span aria-hidden="true">‹</span> {g.away.abbr ?? g.away.name} @ {g.home.abbr ?? g.home.name}, every play
        </Link>
        <h1>Same-game parlay</h1>
        <div className="scorebug">
          <span>{[score, g.status_text].filter(Boolean).join(" · ")}</span>
          {offline && <span className="fresh red" role="alert">Can&apos;t refresh right now. Retrying.</span>}
        </div>
      </div>
      <article id="parlay-card"
               className={`ticket tone-${TONE[view.outcome][0]}${cashed ? " cashed" : ""}`}
               aria-label={`Parlay: ${view.outcome_text}`}>
        <div className="ticket-body">
          <div className="ticket-top">
            <span className="eyebrow">
              {view.legs.length} legs{view.book_name ? ` · ${view.book_name}` : ""}
            </span>
            <StatusChip outcome={view.outcome} text={view.outcome_text} />
          </div>
          <ol className="lights" aria-label={`${lit} of ${view.legs.length} legs cashed`}>
            {view.legs.map((l) => (
              <li key={l.key} className={`light ${light(l.outcome)}`} title={`${l.who}: ${l.outcome_text}`} />
            ))}
          </ol>
          {view.returns
            ? <Payout outcome={view.outcome} back={view.back} returns={view.returns}
                      count={cashed ? true : undefined} />
            : <p className="payout none">{view.headline}</p>}
          {view.fair_returns && (
            <p className="ticket-detail">Without the books&apos; margins: $10 → ${view.fair_returns}.</p>
          )}
          <ol className="legs">
            {view.legs.map((l) => (
              <li key={l.key}>
                <div className="leg-main">
                  <span>
                    <Link href={legCardHref(g.id, l.key)}>{l.who}</Link>: {l.bet}
                  </span>
                  <StatusChip outcome={l.outcome} text={l.outcome_text} />
                </div>
                {progress(l) && <p className="ticket-detail">{progress(l)}</p>}
                <p className="ticket-price">
                  {l.price ? (
                    <>
                      <span className="num">{american(l.price.american)}</span> at {l.price.book_name}
                      {l.price.point !== null && l.line !== null && l.price.point !== l.line
                        ? ` (line ${l.price.point})` : ""}
                      {`, ${l.price.minutes_before_kickoff} min before kickoff`}
                      {l.price.timing === "early"
                        && <span className="flag"> (earlier than usual)</span>}
                    </>
                  ) : l.no_price}
                </p>
              </li>
            ))}
          </ol>
          <ul className="sgp-notes">{view.notes.map((n) => <li key={n}>{n}</li>)}</ul>
        </div>
        <div className="ticket-tear" aria-hidden="true" />
        <div className="ticket-stub">
          {view.returns && (
            <div className="ticket-actions">
              <ShareButton path={sharePath}
                           text={`${view.legs.length}-leg parlay: ${payoutText(view)} (hypothetical)`} />
            </div>
          )}
          <p className="hypo-line">Tap a name for that bet&apos;s own card, with the other books&apos; prices.</p>
          <p className="hypo-line">{view.disclaimer}</p>
        </div>
      </article>
      <RoundRobin view={view} />
      <p><Link className="pill" href="/"><span aria-hidden="true">←</span> Look up another play</Link></p>
    </>
  );
}
