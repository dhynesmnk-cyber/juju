import CountUp from "@/components/CountUp";
import ParlayButton from "@/components/ParlayButton";
import ShareButton from "@/components/ShareButton";
import Jujus from "@/components/world/Jujus";
import type { Mood } from "@/components/world/worlds";
import LocalTime from "@/components/LocalTime";
import { american, quarter } from "@/lib/format";
import type { TrayLeg } from "@/lib/parlayTray";
import type { Card, Outcome } from "@/lib/types";

export const TONE: Record<Outcome, [string, string]> = {
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

/** Notes that change what the card means stay in view; the rest go in Details. */
function splitNotes(notes: string[]): [string[], string[]] {
  const shown = notes.filter((n) => n.startsWith("Corrected"));
  return [shown, notes.filter((n) => !shown.includes(n))];
}

/** One bet: what it was, where it stands, and what $10 paid at the price 45 minutes before
 *  kickoff. The rest (other books, the price without the book's margin, when it was priced,
 *  the next-day check) is one tap away, in Details. */
export default function ResultCard({ card, changed, crew, countUp, id, sharePath, parlay }: {
  card: Card; changed?: boolean; crew?: Mood; countUp?: boolean; id?: string; sharePath?: string;
  parlay?: { game: number; leg: TrayLeg };
}) {
  const [tone, icon] = TONE[card.outcome];
  const p = card.price;
  const detail = progress(card);
  const cashed = !!p && (card.outcome === "locked" || card.outcome === "won");
  const [shownNotes, moreNotes] = splitNotes(card.notes);
  const article = (
    <article id={id}
             className={`card${card.touched ? " touched" : ""}${changed ? " changed" : ""}${cashed ? " cashed" : ""}`}
             aria-label={`${card.label}: ${card.outcome_text}`}>
      <div className="card-top">
        <div className="label">{card.label}</div>
        <span className={`status-chip ${tone}`}>
          <span aria-hidden="true">{icon}</span> {card.outcome_text}
        </span>
      </div>
      <div className="bet">{card.bet}</div>
      <div className="headline">
        {cashed && card.returns ? <CountUp to={card.returns} run={!!countUp} /> : card.headline}
      </div>
      {detail && <div className="detail">{detail}</div>}
      {card.decided_by && <div className="decided">{decidedBy(card.decided_by)}</div>}
      {p ? (
        <div className="detail">
          {american(p.american)} at {p.book_name}
          {p.point !== null && card.line !== null && p.point !== card.line ? ` (line ${p.point})` : ""}
          {`, ${p.minutes_before_kickoff} min before kickoff`}
          {p.timing !== "on_time" && <span className="flag"> (earlier than usual)</span>}
        </div>
      ) : (
        <div className="detail">{card.no_price}</div>
      )}
      {shownNotes.length > 0 && <ul className="notes">{shownNotes.map((n) => <li key={n}>{n}</li>)}</ul>}
      {p && (sharePath || parlay) && (
        <div className="share-row">
          {sharePath && (
            <ShareButton path={sharePath} text={`${card.bet}: ${card.headline} (hypothetical)`} />
          )}
          {parlay && <ParlayButton game={parlay.game} leg={parlay.leg} />}
        </div>
      )}
      {p && (
        <details className="more">
          <summary>Details</summary>
          <dl>
            {card.others.length > 0 && (
              <>
                <dt>Other books</dt>
                <dd>{card.others.map((o) => `${o.book_name} ${american(o.american)}${o.point && o.point !== card.line ? ` (${o.point})` : ""}`).join(" · ")}</dd>
              </>
            )}
            {card.fair_returns && (
              <>
                <dt>Without the book&apos;s margin</dt>
                <dd>
                  $10 → ${card.fair_returns}
                  {card.returns ? `. The margin cost $${(Number(card.fair_returns) - Number(card.returns)).toFixed(2)}.` : ""}
                </dd>
              </>
            )}
            {card.fair_note && (<><dt>Fair value</dt><dd>{card.fair_note}</dd></>)}
            <dt>Priced</dt>
            <dd>
              <LocalTime iso={p.observed_at} />
              {p.timing !== "on_time" ? ", the latest price Juju has before the 45-minute mark" : ""}
              {p.source === "historical" ? ", from the odds vendor's history" : ""}
            </dd>
            {card.verified && (<><dt>Checked</dt><dd>The number matches the official stats.</dd></>)}
          </dl>
          {moreNotes.length > 0 && <ul className="notes">{moreNotes.map((n) => <li key={n}>{n}</li>)}</ul>}
        </details>
      )}
      <p className="hypo-line">Hypothetical. Juju is not a sportsbook.</p>
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
