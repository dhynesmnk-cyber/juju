import CountUp from "@/components/CountUp";
import { CASHED, LOST } from "@/components/StatusChip";
import type { Outcome } from "@/lib/types";

/** The headline of every ticket: what $10 came back as, or would. Both numbers are the API's
 *  own strings (`back` once the outcome is known, `returns` while it isn't), never worked out
 *  here. `count`: a cashed payout that may count up to its number (components/CountUp). */
export default function Payout({ outcome, back, returns, count, small = false }: {
  outcome: Outcome; back: string | null; returns: string | null; count?: boolean;
  small?: boolean;
}) {
  if (returns === null) return <p className="payout none">No price on file</p>;
  const open = back === null;
  const amount = back ?? returns;
  const tone = CASHED.has(outcome) ? " good" : LOST.has(outcome) ? " bad" : "";
  return (
    <p className={`payout${tone}${small ? " small" : ""}`}>
      <span className="payout-label">{open ? "$10 would return" : "$10 would have returned"}</span>{" "}
      <span className="payout-line">
        {count !== undefined && CASHED.has(outcome)
          ? <CountUp to={amount} run={count} />
          : <span className="payout-amount">${amount}</span>}
        {open && <span className="payout-if"> if it hits</span>}
      </span>
      {LOST.has(outcome) && <span className="payout-had"> Had it hit: ${returns}.</span>}
    </p>
  );
}

/** The same headline as one plain sentence, for a share and a screen reader. */
export function payoutText(c: { back: string | null; returns: string | null }): string {
  if (c.returns === null) return "No price on file";
  return c.back === null ? `$10 would return $${c.returns} if it hits`
    : `$10 would have returned $${c.back}`;
}
