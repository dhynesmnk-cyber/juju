// A round robin: every smaller parlay that can be made from the 3 or 4 legs of the parlay on
// screen, $10 on each. Worked out in the browser from that parlay's own response: the same
// legs, each at the price the card already shows, at the parlay's one book. No endpoint is
// asked for anything, and no price appears that the card doesn't show. The money is exact:
// whole numbers only, rounded half up to the cent as backend/juju/core/odds.py `payout` does,
// and the outcome rules are backend/juju/core/parlay.py's. No "@/" imports: `npm test` runs
// the lib tests with Node's own test runner.
import type { Outcome } from "./types.ts";

export interface RobinLeg { key: string; who: string; american: number; outcome: Outcome }

export interface Combo {
  legs: RobinLeg[];
  outcome: Outcome;
  returns: string | null;  // what $10 returns if every counted leg wins; null if none counts
  back: string | null;     // what it comes back as on this outcome; null while undecided
}

export interface Robin { size: number; combos: Combo[]; staked: string; back: string | null }

const DROPS = new Set<Outcome>(["push", "void"]);

/** American odds as decimal odds, a fraction: +150 is 250/100, -120 is 220/120. */
function fraction(american: number): [bigint, bigint] {
  if (!Number.isInteger(american) || Math.abs(american) < 100) throw new Error("bad odds");
  return american > 0 ? [BigInt(100 + american), 100n]
    : [BigInt(100 - american), BigInt(-american)];
}

function cents(n: bigint): string {
  return `${n / 100n}.${String(n % 100n).padStart(2, "0")}`;
}

/** $10 at the product of these odds, to the cent, half up. */
export function payout(americans: number[]): string {
  let num = 1000n, den = 1n;  // $10.00 in cents
  for (const a of americans) {
    const [n, d] = fraction(a);
    num *= n; den *= d;
  }
  return cents((2n * num + den) / (2n * den));
}

/** backend/juju/core/parlay.py `parlay_outcome`, rule for rule. */
export function parlayOutcome(legs: Outcome[]): Outcome {
  if (legs.includes("lost")) return "lost";
  if (legs.includes("gone")) return "gone";
  const counted = legs.filter((o) => !DROPS.has(o));
  if (!counted.length) return legs.every((o) => o === "void") ? "void" : "push";
  if (counted.every((o) => o === "won")) return "won";
  for (const undecided of ["no_stat_line", "unavailable", "untracked"] as const) {
    if (counted.includes(undecided)) return undecided;
  }
  if (counted.every((o) => o === "won" || o === "locked")) return "locked";
  if (counted.every((o) => o === "pregame")) return "pregame";
  return "live";
}

function combo(legs: RobinLeg[]): Combo {
  const outcome = parlayOutcome(legs.map((l) => l.outcome));
  const counted = legs.filter((l) => !DROPS.has(l.outcome));
  const returns = payout(counted.map((l) => l.american));
  const back = outcome === "won" || outcome === "locked" ? returns
    : outcome === "lost" || outcome === "gone" ? "0.00"
    : outcome === "push" || outcome === "void" ? "10.00" : null;
  return { legs, outcome, returns, back };
}

function choose<T>(items: T[], k: number): T[][] {
  if (k === 0) return [[]];
  if (items.length < k) return [];
  const [first, ...rest] = items;
  return [...choose(rest, k - 1).map((c) => [first, ...c]), ...choose(rest, k)];
}

function toCents(s: string): bigint {
  const [whole, frac = "00"] = s.split(".");
  return BigInt(whole) * 100n + BigInt(frac);
}

/** The round robins of 3 or 4 legs: by twos, and by threes for four. Empty for anything else. */
export function roundRobins(legs: RobinLeg[]): Robin[] {
  if (legs.length < 3 || legs.length > 4) return [];
  const out: Robin[] = [];
  for (let size = 2; size < legs.length; size++) {
    const combos = choose(legs, size).map(combo);
    const decided = combos.every((c) => c.back !== null);
    out.push({
      size, combos, staked: cents(BigInt(combos.length) * 1000n),
      back: decided ? cents(combos.reduce((sum, c) => sum + toCents(c.back ?? "0"), 0n)) : null,
    });
  }
  return out;
}
