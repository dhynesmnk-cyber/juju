"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import CountUp from "@/components/CountUp";
import { TONE } from "@/components/ResultCard";
import WorldStage from "@/components/world/WorldStage";
import { worldFor } from "@/components/world/worlds";
import { legCardHref } from "@/lib/parlayTray";
import type { Outcome, ParlayLeg, ParlayView as View } from "@/lib/types";

const SETTLED = new Set<Outcome>(["won", "lost", "push", "void", "no_stat_line"]);

/** Each leg is a light in the sky: lit once it has cashed, out once it has lost. */
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
  return `${Number(leg.current)}`;
}

/** The parlay card: the price at once, the status every 10 s until it settles. The gold
 *  shockwave fires only when every leg is lit. */
export default function ParlayView({ initial, apiPath }: { initial: View; apiPath: string }) {
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
          setAnnounce(`Parlay: ${next.outcome_text}. ${next.headline}`);
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
  const [tone, icon] = TONE[view.outcome];
  const cashed = view.outcome === "locked" || view.outcome === "won";
  const lit = view.legs.filter((l) => light(l.outcome) === "lit").length;
  return (
    <>
      <WorldStage world={worldFor(view.outcome, view.returns !== null)} shockAt={shockAt}
                  shockFrom={shockFrom} />
      <div className="sr-only" aria-live="polite">{announce}</div>
      <div className="result-head">
        <h1>Same-game parlay</h1>
        <div className="sub">{[score, g.status_text].filter(Boolean).join(" · ")}</div>
        {offline && <div className="fresh red" role="alert">Can&apos;t refresh right now. Retrying.</div>}
      </div>
      <ol className="sky" aria-label={`${lit} of ${view.legs.length} legs cashed`}>
        {view.legs.map((l) => (
          <li key={l.key} className={`light ${light(l.outcome)}`} title={`${l.who}: ${l.outcome_text}`} />
        ))}
      </ol>
      <article id="parlay-card" className={`card${cashed ? " cashed" : ""}`}
               aria-label={`Parlay: ${view.outcome_text}`}>
        <div className="label">
          {view.legs.length} legs{view.book_name ? ` · ${view.book_name}` : ""}
        </div>
        <span className={`status-chip ${tone}`}>
          <span aria-hidden="true">{icon}</span> {view.outcome_text}
        </span>
        <div className="headline">
          {cashed && view.returns ? <CountUp to={view.returns} run /> : view.headline}
        </div>
        {view.fair_returns && (
          <div className="fair">Without the books&apos; margins: $10 → ${view.fair_returns}.</div>
        )}
        <ol className="legs">
          {view.legs.map((l) => {
            const [legTone, legIcon] = TONE[l.outcome];
            return (
              <li key={l.key}>
                <div>
                  <Link href={legCardHref(g.id, l.key)}>{l.who}</Link>: {l.bet}
                </div>
                <span className={`status-chip ${legTone}`}>
                  <span aria-hidden="true">{legIcon}</span> {l.outcome_text}
                </span>
                <div className="detail">
                  {[progress(l), l.book_name ? `priced at ${l.book_name}` : l.no_price,
                    l.timing === "early" ? "earlier than T-45" : null].filter(Boolean).join(" · ")}
                </div>
              </li>
            );
          })}
        </ol>
        <ul className="notes">{view.notes.map((n) => <li key={n}>{n}</li>)}</ul>
      </article>
      <p className="hint">Each leg&apos;s price is on its own card: tap a name.</p>
      <p className="hint hypo">{view.disclaimer}</p>
      <p><Link href={`/`}>← Look up another play</Link></p>
    </>
  );
}
