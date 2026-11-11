import type { ReactNode } from "react";
import Payout from "@/components/Payout";
import StatusChip, { CASHED } from "@/components/StatusChip";
import { american } from "@/lib/format";
import type { Card, RecapParlay, RecapPrice } from "@/lib/types";

type MiniCardData = Pick<Card, "label" | "bet" | "outcome" | "outcome_text" | "current"
  | "needed" | "returns" | "back"> & { price: RecapPrice | null };

function priceLine(p: RecapPrice) {
  return (
    <>
      <span className="num">{american(p.american)}</span> at {p.book_name},{" "}
      {p.minutes_before_kickoff} min before kickoff
      {p.timing !== "on_time" && <span className="flag"> (earlier than usual)</span>}
    </>
  );
}

/** A bet as a small ticket: what it was, how it went, what $10 came back as and at what price.
 *  A list of them (a recap) says "hypothetical" once, so `hypo` is off there. `children`: what
 *  goes under it (Add to parlay on a game's page; the deciding play in a recap). */
export default function MiniCard({ c, hypo = true, children }: {
  c: MiniCardData; hypo?: boolean; children?: ReactNode;
}) {
  return (
    <article className={`mini-card${CASHED.has(c.outcome) ? " cashed" : ""}`}
             aria-label={`${c.label}: ${c.outcome_text}`}>
      <div className="mini-top">
        <span className="mini-bet">{c.bet}</span>
        <StatusChip outcome={c.outcome} text={c.outcome_text} />
      </div>
      <Payout outcome={c.outcome} back={c.back} returns={c.returns} small />
      {c.outcome === "live" && c.current !== null && c.needed !== null && (
        <p className="ticket-detail">{Number(c.current)} so far. Needs {Number(c.needed)} more.</p>
      )}
      {c.price && <p className="ticket-price">{priceLine(c.price)}</p>}
      {children}
      {hypo && <p className="hypo-line">Hypothetical. Juju is not a sportsbook.</p>}
    </article>
  );
}

/** A same-game parlay in a recap: its result, each leg at the parlay's book, and the note
 *  every parlay carries. */
export function ParlayMini({ p }: { p: RecapParlay }) {
  return (
    <article className={`mini-card${CASHED.has(p.outcome) ? " cashed" : ""}`}
             aria-label={`Same-game parlay: ${p.outcome_text}`}>
      <div className="mini-top">
        <span className="mini-bet">{p.legs.length}-leg parlay{p.book_name ? ` at ${p.book_name}` : ""}</span>
        <StatusChip outcome={p.outcome} text={p.outcome_text} />
      </div>
      {p.priced
        ? <Payout outcome={p.outcome} back={p.back} returns={p.returns} small />
        : <p className="payout none small">{p.headline}</p>}
      <ul className="recap-legs">
        {p.legs.map((leg) => (
          <li key={leg.key}>
            <span>{leg.who}: {leg.bet}</span>
            <span className="leg-meta">
              {" "}{leg.outcome_text}{leg.price ? ` · ${american(leg.price.american)}` : ""}
            </span>
          </li>
        ))}
      </ul>
      {p.notes[0] && <p className="sgp-note">{p.notes[0]}</p>}
    </article>
  );
}
