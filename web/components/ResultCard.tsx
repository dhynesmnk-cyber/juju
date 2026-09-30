import CountUp from "@/components/CountUp";
import ShareButton from "@/components/ShareButton";
import Jujus from "@/components/world/Jujus";
import type { Mood } from "@/components/world/worlds";
import { american, localAndEt, quarter } from "@/lib/format";
import type { Card, Outcome } from "@/lib/types";

const TONE: Record<Outcome, [string, string]> = {
  won: ["good", "✓"], locked: ["good", "✓"], lost: ["bad", "✕"], gone: ["bad", "✕"],
  live: ["live", "●"], waiting_for_feed: ["live", "…"], pregame: ["neutral", "◷"],
  push: ["neutral", "="], void: ["neutral", "="], no_stat_line: ["neutral", "–"],
  untracked: ["neutral", "–"], unavailable: ["neutral", "!"],
};

function decidedBy(d: NonNullable<Card["decided_by"]>): string {
  const when = [quarter(d.period), d.clock].filter(Boolean).join(" ");
  if (d.exact) return `Decided by the play at ${when}: ${d.text}`;
  if (d.text) return `On or around: ${d.text}${when ? ` (${when})` : ""}.`;
  return `Went past the line by ${when || "this point"}; the feed didn't say on which play.`;
}

function progress(card: Card): string | null {
  if (card.current === null) {
    return card.outcome === "live" ? "No stat line yet." : null;
  }
  const now = Number(card.current);
  if (card.outcome === "live" && card.needed !== null) {
    return `${now} so far. Needs ${Number(card.needed)} more.`;
  }
  return `${now} so far.`;
}

export default function ResultCard({ card, changed, crew, countUp, id, sharePath }: {
  card: Card; changed?: boolean; crew?: Mood; countUp?: boolean; id?: string; sharePath?: string;
}) {
  const [tone, icon] = TONE[card.outcome];
  const p = card.price;
  const detail = progress(card);
  const cashed = card.outcome === "locked" || card.outcome === "won";
  const article = (
    <article id={id}
             className={`card${card.touched ? " touched" : ""}${changed ? " changed" : ""}${cashed ? " cashed" : ""}`}
             aria-label={`${card.label}: ${card.outcome_text}`}>
      <div className="label">{card.label}</div>
      <div className="bet">{card.bet}</div>
      <span className={`status-chip ${tone}`}>
        <span aria-hidden="true">{icon}</span> {card.outcome_text}
      </span>
      {card.verified && (
        <span className="status-chip neutral verified">
          <span aria-hidden="true">☑</span> Verified
        </span>
      )}
      <div className="headline">
        {cashed && card.returns ? <CountUp to={card.returns} run={!!countUp} /> : card.headline}
      </div>
      {detail && <div className="detail">{detail}</div>}
      {card.decided_by && <div className="decided">{decidedBy(card.decided_by)}</div>}
      {p ? (
        <div className="detail">
          {american(p.american)} at {p.book_name}
          {p.point !== null && card.line !== null && p.point !== card.line ? ` (line ${p.point})` : ""}
          {", priced "}
          {localAndEt(p.observed_at)}{" · "}
          {p.timing === "on_time"
            ? `${p.minutes_before_kickoff} min before kickoff`
            : <span className="flag">{p.minutes_before_kickoff} min before kickoff, earlier than T-45</span>}
          {p.source === "historical" ? " · from The Odds API archive" : ""}
        </div>
      ) : (
        <div className="detail">{card.no_price}</div>
      )}
      {card.fair_returns && (
        <div className="fair">
          Without the book&apos;s margin: $10 → ${card.fair_returns}.
          {card.returns ? ` The margin cost $${(Number(card.fair_returns) - Number(card.returns)).toFixed(2)}.` : ""}
        </div>
      )}
      {card.fair_note && p && <div className="fair muted">{card.fair_note}</div>}
      {card.others.length > 0 && (
        <div className="others">
          Other books: {card.others.map((o) => `${o.book_name} ${american(o.american)}${o.point && o.point !== card.line ? ` (${o.point})` : ""}`).join(" · ")}
        </div>
      )}
      {card.notes.length > 0 && (
        <ul className="notes">{card.notes.map((n) => <li key={n}>{n}</li>)}</ul>
      )}
      {p && <div className="others">Source record {p.provenance}</div>}
      {p && sharePath && (
        <ShareButton path={sharePath} text={`${card.bet}: ${card.headline} (hypothetical)`} />
      )}
    </article>
  );
  if (!crew) return article;
  return (
    <div className="cardwrap">
      <Jujus mood={crew} />
      {article}
    </div>
  );
}
