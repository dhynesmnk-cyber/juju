import { type Credits, creditsText } from "@/lib/liveChat";

const ICON = { unknown: "○", ok: "●", low: "⚠", out: "✕" } as const;

/** The credits The Odds API says are left (never a sum of ours). A warning when low; live
 *  questions are never blocked by it (docs/plans/live-chat.md, item 7). */
export default function CreditsMeter({ credits }: { credits: Credits | null }) {
  const state = credits?.state ?? "unknown";
  return (
    <div className={`credits credits-${state}`} role="status">
      <p className="credits-line">
        <span className="credits-icon" aria-hidden="true">{ICON[state]}</span>
        <span>{creditsText(credits)}</span>
      </p>
      {credits?.note && <p className="credits-note">{credits.note}</p>}
    </div>
  );
}
