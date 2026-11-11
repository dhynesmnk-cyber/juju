import StatusChip from "@/components/StatusChip";
import { payout, roundRobins, type RobinLeg } from "@/lib/roundRobin";
import type { ParlayView } from "@/lib/types";

const TEXT: Partial<Record<string, string>> = {
  won: "Won", locked: "Cashed, if the play stands", lost: "Lost", gone: "Lost, if the play stands",
  live: "Live", pregame: "Not started", push: "Push: stake back", void: "Void: stake back",
};

/** The round robin of the 3 or 4 legs on this parlay: every smaller parlay they make, $10 on
 *  each, worked out here from the prices this card already shows at its one book
 *  (lib/roundRobin.ts). Shown only when one book priced every leg, and only if this page's own
 *  sum of all the legs agrees with the number the API sent: otherwise nothing is shown. */
export default function RoundRobin({ view }: { view: ParlayView }) {
  if (!view.book_name || view.returns === null || view.legs.some((l) => !l.price)) return null;
  const legs: RobinLeg[] = view.legs.map((l) => ({
    key: l.key, who: l.who, american: l.price!.american, outcome: l.outcome,
  }));
  const counted = legs.filter((l) => l.outcome !== "push" && l.outcome !== "void");
  if (payout(counted.map((l) => l.american)) !== view.returns) return null;
  const robins = roundRobins(legs);
  if (!robins.length) return null;
  return (
    <section className="robin" aria-labelledby="robin-title">
      <h2 id="robin-title">Round robin</h2>
      <p className="robin-about">
        The same {legs.length} legs as every smaller parlay, $10 on each, at {view.book_name}.
      </p>
      {robins.map((r) => (
        <div key={r.size} className="robin-group">
          <h3 className="eyebrow">
            By {r.size === 2 ? "twos" : "threes"}: {r.combos.length} parlays, ${r.staked} in
            {r.back !== null ? `, $${r.back} back` : ""}
          </h3>
          <ul>
            {r.combos.map((c) => (
              <li key={c.legs.map((l) => l.key).join(",")}>
                <span className="robin-legs">{c.legs.map((l) => l.who).join(" + ")}</span>
                <span className="robin-result">
                  <StatusChip outcome={c.outcome} text={TEXT[c.outcome] ?? "Undecided"} />
                  <span className="num">
                    ${c.back ?? c.returns}{c.back === null ? " if it hits" : ""}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      ))}
      <p className="sgp-note">{view.notes[0]} That goes for every combination here.</p>
      <p className="hypo-line">Hypothetical. Juju is not a sportsbook.</p>
    </section>
  );
}
