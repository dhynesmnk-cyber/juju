import type { ReactNode } from "react";
import { TONE } from "@/components/ResultCard";
import { american } from "@/lib/format";
import type { Card, RecapParlay, RecapPrice } from "@/lib/types";

type MiniCardData = Pick<Card, "label" | "bet" | "outcome" | "outcome_text" | "headline"
  | "current" | "needed"> & { price: RecapPrice | null };

function priceLine(p: RecapPrice) {
  return (
    <>
      {american(p.american)} at {p.book_name}, {p.minutes_before_kickoff} min before kickoff
      {p.timing !== "on_time" && <span className="flag"> (earlier than usual)</span>}
    </>
  );
}

/** A bet in a few lines: what it was, how it went, what $10 paid and at what price. A list of
 *  them (a recap) says "hypothetical" once, so `hypo` is off there. */
export default function MiniCard({ c, hypo = true, children }: {
  c: MiniCardData; hypo?: boolean; children?: ReactNode;
}) {
  const [tone, icon] = TONE[c.outcome];
  const cashed = c.outcome === "locked" || c.outcome === "won";
  return (
    <article className={`mini-card${cashed ? " cashed" : ""}`} aria-label={`${c.label}: ${c.outcome_text}`}>
      <div className="mini-top">
        <span className="muted">{c.bet}</span>
        <span className={`status-chip ${tone}`}><span aria-hidden="true">{icon}</span> {c.outcome_text}</span>
      </div>
      <div className="headline">{c.headline}</div>
      {c.outcome === "live" && c.current !== null && c.needed !== null && (
        <div className="detail">{Number(c.current)} so far. Needs {Number(c.needed)} more.</div>
      )}
      {c.price && <div className="detail">{priceLine(c.price)}</div>}
      {children}
      {hypo && <p className="hypo-line">Hypothetical. Juju is not a sportsbook.</p>}
    </article>
  );
}

/** A same-game parlay in a recap: its result, and each leg at the parlay's book. */
export function ParlayMini({ p }: { p: RecapParlay }) {
  const [tone, icon] = TONE[p.outcome];
  const cashed = p.outcome === "locked" || p.outcome === "won";
  return (
    <article className={`mini-card${cashed ? " cashed" : ""}`}
             aria-label={`Same-game parlay: ${p.outcome_text}`}>
      <div className="mini-top">
        <span className="muted">{p.legs.length}-leg parlay{p.book_name ? ` at ${p.book_name}` : ""}</span>
        <span className={`status-chip ${tone}`}><span aria-hidden="true">{icon}</span> {p.outcome_text}</span>
      </div>
      <div className="headline">{p.headline}</div>
      <ul className="recap-legs">
        {p.legs.map((leg) => (
          <li key={leg.key}>
            <span>{leg.who}: {leg.bet}</span>
            <span className="muted small">
              {" "}{leg.outcome_text}{leg.price ? ` · ${american(leg.price.american)}` : ""}
            </span>
          </li>
        ))}
      </ul>
    </article>
  );
}
