// `npm test`: Node's own test runner, no network.
import assert from "node:assert/strict";
import { test } from "node:test";
import { byEtDay, etDay } from "./format.ts";

test("a kickoff's day is the NFL's, in Eastern time, wherever the page renders", () => {
  assert.equal(etDay("2026-10-18T17:00:00Z"), "Sunday, Oct 18");
  // Sunday night football: already Monday in UTC, still Sunday on the NFL's calendar.
  assert.equal(etDay("2026-10-19T00:20:00Z"), "Sunday, Oct 18");
  assert.equal(etDay("2026-10-20T00:15:00Z"), "Monday, Oct 19");
});

test("upcoming games group into days, in the order given", () => {
  const games = [
    { id: 1, commence_time: "2026-10-18T17:00:00Z" },
    { id: 2, commence_time: "2026-10-19T00:20:00Z" },
    { id: 3, commence_time: "2026-10-20T00:15:00Z" },
  ];
  assert.deepEqual(byEtDay(games).map(([day, gs]) => [day, gs.map((g) => g.id)]), [
    ["Sunday, Oct 18", [1, 2]],
    ["Monday, Oct 19", [3]],
  ]);
});

test("a recap's totals count each bet once, and the money only once it's settled", async () => {
  const { totalsLine } = await import("./format.ts");
  const t = { bets: 4, won: 3, lost: 1, stake_back: 0, open: 0, unpriced: 0, staked: "40.00",
              back: "52.10" };
  assert.equal(totalsLine(t),
               "4 bets: 3 won, 1 lost. $10 on each settled bet: $40.00 in, $52.10 back.");
  assert.equal(totalsLine({ ...t, bets: 1, won: 0, lost: 0, open: 1, staked: "0.00",
                            back: "0.00" }), "1 bet: 1 not settled yet.");
  assert.equal(totalsLine({ ...t, unpriced: 2 }).endsWith(" 2 more had no price on file."), true);
  assert.equal(totalsLine({ ...t, bets: 0, won: 0, lost: 0 }), "No bets to count yet.");
});

test("a score line, or the matchup before kickoff", async () => {
  const { scoreline } = await import("./format.ts");
  const side = (abbr: string, score: number | null) => ({ abbr, name: abbr, score });
  assert.equal(scoreline({ label: "PHI @ CHI", away: side("PHI", 7), home: side("CHI", 27) }),
               "PHI 7 – 27 CHI");
  assert.equal(scoreline({ label: "PHI @ CHI", away: side("PHI", null), home: side("CHI", null) }),
               "PHI @ CHI");
});
