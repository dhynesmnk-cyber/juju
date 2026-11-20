import {
  type LegView, type PickView, type Turn, clockTime, isProblem, isStale, oppositeSide,
  priceText, returnsText,
} from "@/lib/liveChat";

function PickCard({ leg, pick, nearest }: { leg: LegView; pick: PickView; nearest: boolean }) {
  const other = oppositeSide(leg.side);
  return (
    <article className="live-card" aria-label={pick.bet}>
      <p className="eyebrow">{leg.label}{nearest ? " · nearest posted line" : ""}</p>
      <p className="live-bet">{pick.bet}</p>
      <p className="live-price">
        <span className="live-odds num">{priceText(pick.book)}</span>
        <span>at {pick.book.book_name}</span>
      </p>
      <p className="live-returns num">{returnsText(pick.book)}</p>
      {pick.opposite && other && (
        <p className="small muted">{other} {pick.point ?? ""}: {priceText(pick.opposite)}</p>
      )}
      {pick.others.length > 0 && (
        <ul className="book-list" aria-label="Other books">
          {pick.others.map((p) => (
            <li key={p.book}><span>{p.book_name}</span><span className="num">{priceText(p)}</span></li>
          ))}
        </ul>
      )}
    </article>
  );
}

/** One question and its answer: the plain-language reply, then each line's price at the book of
 *  record with the other books beneath. Every number is the API's. */
export default function LiveAnswerView({ turn, now, onAskAgain }: {
  turn: Turn;
  now: number;
  onAskAgain: (text: string) => void;
}) {
  const { answer, error } = turn;
  const pending = answer === null && error === null;
  const problem = answer ? isProblem(answer.kind) : false;
  const stale = isStale(answer, now);
  const said = (answer?.sentences ?? []).filter((s) => !s.startsWith("Other books:"));
  const cards = (answer?.legs ?? []).filter((l) => l.available);
  return (
    <li className="turn">
      <p className="you">{turn.text}</p>
      <div className={`reply${problem || error ? " problem" : ""}${stale ? " stale" : ""}`}
           aria-busy={pending}>
        {pending && <p className="muted" role="status">Checking live prices…</p>}
        {error && <p>{error}</p>}
        {answer && (
          <>
            {answer.ai && (
              <p className="ai-tag small">
                AI-written reply. Every price and number is checked against the live books.
              </p>
            )}
            {said.map((s, i) => <p key={i}>{s}</p>)}
            {!problem && cards.map((leg) => leg.picks.map((pick) => (
              <PickCard key={`${leg.market}-${pick.point}-${pick.bet}`} leg={leg} pick={pick}
                        nearest={!leg.exact} />
            )))}
            {answer.as_of && (
              <p className="as-of small">
                {problem ? "Checked at" : "Live price as of"}{" "}
                <time dateTime={answer.as_of}>{clockTime(answer.as_of)}</time>
                {answer.cached ? " · from the last minute's prices" : ""}
                {" · "}{answer.cost === 0 ? "no credits used"
                  : `${answer.cost} credit${answer.cost === 1 ? "" : "s"} used`}
              </p>
            )}
            {stale && (
              <p className="stale-note" role="status">
                This price is over a minute old and may have moved.{" "}
                <button type="button" className="link-button" onClick={() => onAskAgain(turn.text)}>
                  Ask again
                </button>
              </p>
            )}
          </>
        )}
      </div>
    </li>
  );
}
