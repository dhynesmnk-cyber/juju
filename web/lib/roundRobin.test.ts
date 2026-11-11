import assert from "node:assert/strict";
import { test } from "node:test";
import { parlayOutcome, payout, roundRobins, type RobinLeg } from "./roundRobin.ts";
import type { Outcome } from "./types.ts";

test("the money is the backend's, to the cent", () => {
  // backend/tests: Barkley -112 alone is $18.93; with Smith -120 and Keenum -113, $65.41
  // (the recorded game's three-leg parlay, as /api/parlay returns it).
  assert.equal(payout([-112]), "18.93");
  assert.equal(payout([-112, -120, -113]), "65.41");
  assert.equal(payout([150]), "25.00");
  assert.equal(payout([-110, 150]), "47.73");  // 10 * 1.90909... * 2.5 = 47.727..., half up
  assert.equal(payout([]), "10.00");
  assert.throws(() => payout([50]));
});

test("the outcome rules are core/parlay.py's table", () => {
  const cases: [Outcome[], Outcome][] = [
    [["won", "won"], "won"], [["won", "lost"], "lost"], [["locked", "gone"], "gone"],
    [["live", "lost"], "lost"], [["locked", "locked"], "locked"], [["locked", "live"], "live"],
    [["won", "push"], "won"], [["push", "void"], "push"], [["void", "void"], "void"],
    [["won", "no_stat_line"], "no_stat_line"], [["locked", "unavailable"], "unavailable"],
    [["locked", "untracked"], "untracked"], [["pregame", "pregame"], "pregame"],
  ];
  for (const [legs, result] of cases) assert.equal(parlayOutcome(legs), result, legs.join("+"));
});

const leg = (key: string, american: number, outcome: Outcome): RobinLeg =>
  ({ key, who: key, american, outcome });

test("three legs make three twos; four make six twos and four threes", () => {
  const three = roundRobins([leg("a", -112, "won"), leg("b", -120, "won"), leg("c", -113, "lost")]);
  assert.equal(three.length, 1);
  assert.deepEqual(three[0].combos.map((c) => c.legs.map((l) => l.key).join("")), ["ab", "ac", "bc"]);
  assert.equal(three[0].staked, "30.00");
  // Only a+b came in: 10 * (212/112) * (220/120) = 34.70; the two with the lost leg pay nothing.
  assert.deepEqual(three[0].combos.map((c) => c.back), ["34.70", "0.00", "0.00"]);
  assert.equal(three[0].back, "34.70");
  const four = roundRobins([leg("a", -110, "live"), leg("b", 120, "locked"), leg("c", -105, "locked"),
                            leg("d", 100, "live")]);
  assert.deepEqual(four.map((r) => [r.size, r.combos.length, r.staked]),
                   [[2, 6, "60.00"], [3, 4, "40.00"]]);
  assert.equal(four[0].back, null);  // some are still live: no total yet
  assert.equal(four[0].combos.find((c) => c.legs.map((l) => l.key).join("") === "bc")?.back,
               payout([120, -105]));
});

test("a pushed leg drops out of its combinations; other sizes have no round robin", () => {
  const [by2] = roundRobins([leg("a", -110, "won"), leg("b", 150, "push"), leg("c", -120, "won")]);
  const ab = by2.combos[0];
  assert.equal(ab.outcome, "won");
  assert.equal(ab.back, payout([-110]));
  assert.deepEqual(roundRobins([leg("a", -110, "won"), leg("b", 150, "won")]), []);
  assert.deepEqual(roundRobins(Array.from({ length: 5 }, (_, i) => leg(`l${i}`, -110, "won"))), []);
});
