import type { Outcome } from "@/lib/types";

/** How each outcome looks: a tone (its colour) and an icon. A status is always the words and
 *  the icon, never the colour alone (docs/GOALS.md section 7.7). */
export const TONE: Record<Outcome, [string, string]> = {
  won: ["good", "✓"], locked: ["good", "✓"], lost: ["bad", "✕"], gone: ["bad", "✕"],
  live: ["live", "●"], waiting_for_feed: ["live", "…"], pregame: ["neutral", "◷"],
  push: ["neutral", "="], void: ["neutral", "="], no_stat_line: ["neutral", "–"],
  untracked: ["neutral", "–"], unavailable: ["neutral", "!"],
};

export const CASHED: ReadonlySet<Outcome> = new Set(["locked", "won"]);
export const LOST: ReadonlySet<Outcome> = new Set(["lost", "gone"]);

export default function StatusChip({ outcome, text }: { outcome: Outcome; text: string }) {
  const [tone, icon] = TONE[outcome];
  return (
    <span className={`status-chip ${tone}`}>
      <span className="chip-icon" aria-hidden="true">{icon}</span> {text}
    </span>
  );
}
