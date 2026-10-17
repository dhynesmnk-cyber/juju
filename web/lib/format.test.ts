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
