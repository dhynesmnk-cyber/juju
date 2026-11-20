// `npm test`: Node's own test runner, no network.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  type Credits, type LiveAnswer, type StorageLike, type Turn, MAX_TURNS, STALE_AFTER_MS,
  ageMs, askErrorText, clockTime, creditsText, forgetEndedThreads, historyFor, isStale, loadThread,
  isProblem, newTurnId, normalizeAnswer, oppositeSide, priceText, returnsText, saveThread,
  threadKey,
} from "./liveChat.ts";

function memory(initial: Record<string, string> = {}): StorageLike & { data: Map<string, string> } {
  const data = new Map(Object.entries(initial));
  return {
    data,
    getItem: (k) => data.get(k) ?? null,
    setItem: (k, v) => void data.set(k, v),
    removeItem: (k) => void data.delete(k),
    key: (i) => [...data.keys()][i] ?? null,
    get length() { return data.size; },
  };
}

const credits = (over: Partial<Credits> = {}): Credits =>
  ({ remaining: 19_998, state: "ok", low_at: 2000, spent: 2, note: null, ...over });

const turn = (n: number, answer: LiveAnswer | null = null): Turn =>
  ({ id: `t${n}`, at: 1_000 + n, text: `question ${n}`, answer, error: null });

const answerAt = (asOf: string | null): LiveAnswer => ({
  kind: "answer", message: "m", sentences: ["m"], legs: [], parlay: null, choices: [],
  as_of: asOf, cached: false, cost: 1, credits: credits(), disclaimer: "d", ai: false,
});

test("a price a minute old is stale, one a second younger is not", () => {
  const at = "2026-11-20T21:42:00Z";
  const t0 = Date.parse(at);
  assert.equal(isStale(answerAt(at), t0 + STALE_AFTER_MS - 1), false);
  assert.equal(isStale(answerAt(at), t0 + STALE_AFTER_MS), true);
  assert.equal(isStale(answerAt(at), t0 + 5 * STALE_AFTER_MS), true);
  assert.equal(ageMs(at, t0 - 5000), 0);                  // a clock a little behind: never negative
  assert.equal(isStale(answerAt(null), t0), false);       // nothing priced, nothing to go stale
  assert.equal(isStale(null, t0), false);
  assert.equal(isStale(answerAt("not a date"), t0), false);
});

test("the odds' own 60 seconds is the staleness limit", () => {
  assert.equal(STALE_AFTER_MS, 60_000);                   // backend/juju/ingest/live_odds.py
});

test("credits are said as The Odds API's count, with a warning when low", () => {
  assert.equal(creditsText(credits()), "19,998 odds credits left");
  assert.equal(creditsText(credits({ remaining: 1500, state: "low" })),
               "1,500 odds credits left: running low");
  assert.equal(creditsText(credits({ remaining: 0, state: "out" })), "Out of odds credits");
  assert.equal(creditsText(credits({ remaining: null, state: "unknown" })),
               "Odds credits: shown after your first question");
  assert.equal(creditsText(null), "Odds credits: shown after your first question");
});

test("prices read as the API gave them", () => {
  assert.equal(priceText({ american: 170 }), "+170");
  assert.equal(priceText({ american: -125 }), "-125");
  assert.equal(returnsText({ returns: "18.00" }), "$10 → $18.00");
  assert.equal(returnsText({ returns: "1234.5" }), "$10 → $1,234.50");
});

test("the time an answer is from is shown in the viewer's zone", () => {
  assert.equal(clockTime("2026-11-20T21:42:00Z", "America/New_York"), "4:42 PM");
  assert.equal(clockTime("2026-11-20T21:42:00Z", "America/Los_Angeles"), "1:42 PM");
});

test("a failed question says why, never a made-up price", () => {
  assert.match(askErrorText(401), /passcode/);
  assert.match(askErrorText(404), /no longer on the live schedule/);
  assert.match(askErrorText(422), /another way/);
  assert.match(askErrorText(503), /no price was made up/);
  assert.match(askErrorText(null), /Try again/);
  assert.doesNotMatch(askErrorText(500), /\$/);
});

test("a thread is kept per game and comes back as it was left", () => {
  const store = memory();
  const turns = [turn(1, answerAt("2026-11-20T21:42:00Z")), turn(2)];
  saveThread(store, "game-a", turns);
  assert.deepEqual(loadThread(store, "game-a"), turns);
  assert.deepEqual(loadThread(store, "game-b"), []);
  assert.ok(store.data.has(threadKey("game-a")));
});

test("a thread keeps only the last forty turns", () => {
  const store = memory();
  saveThread(store, "g", Array.from({ length: MAX_TURNS + 10 }, (_, i) => turn(i)));
  const back = loadThread(store, "g");
  assert.equal(back.length, MAX_TURNS);
  assert.equal(back[0]?.id, "t10");
});

test("an empty thread removes its saved copy", () => {
  const store = memory();
  saveThread(store, "g", [turn(1)]);
  saveThread(store, "g", []);
  assert.equal(store.data.size, 0);
});

test("a damaged, foreign or missing store never breaks the page", () => {
  for (const raw of ["{not json", "42", "null", '"text"', '{"a":1}']) {
    assert.deepEqual(loadThread(memory({ [threadKey("g")]: raw }), "g"), [], raw);
  }
  const mixed = JSON.stringify([turn(1), { id: 5 }, null, "x", turn(2)]);
  assert.deepEqual(loadThread(memory({ [threadKey("g")]: mixed }), "g").map((t) => t.id),
                   ["t1", "t2"]);
  assert.deepEqual(loadThread(null, "g"), []);
  assert.doesNotThrow(() => saveThread(null, "g", [turn(1)]));
  const blocked: StorageLike = {
    getItem() { throw new Error("blocked"); }, setItem() { throw new Error("full"); },
    removeItem() { throw new Error("blocked"); }, key() { throw new Error("blocked"); },
    length: 0,
  };
  assert.deepEqual(loadThread(blocked, "g"), []);
  assert.doesNotThrow(() => saveThread(blocked, "g", [turn(1)]));
  assert.doesNotThrow(() => forgetEndedThreads(blocked, ["g"]));
});

test("the threads of games that have left the schedule are dropped, and nothing else", () => {
  const store = memory({
    [threadKey("on")]: "[]", [threadKey("over")]: "[]", "juju-other": "keep", juju21: "yes",
  });
  forgetEndedThreads(store, ["on"]);
  assert.deepEqual([...store.data.keys()].sort(), ["juju-other", "juju21", threadKey("on")].sort());
});

test("turn ids differ", () => {
  assert.notEqual(newTurnId(1000, () => 0.123456), newTurnId(1000, () => 0.654321));
  assert.match(newTurnId(1000, () => 0.5), /^[a-z0-9]+-[a-z0-9]+$/);
});

test("a failure the API explains is filled in so the page can draw it", () => {
  const a = normalizeAnswer({ kind: "out_of_credits", message: "Juju's odds credits are used up.",
                              credits: credits({ remaining: 0, state: "out" }) });
  assert.deepEqual([a.legs, a.parlay, a.choices, a.cost, a.cached, a.as_of],
                   [[], null, [], 0, true, null]);
  assert.deepEqual(a.sentences, ["Juju's odds credits are used up."]);
  assert.equal(isStale(a, Date.now()), false);         // no price on it, so nothing is old
  assert.equal(isProblem(a.kind), true);
  const full = answerAt("2026-11-20T21:42:00Z");
  assert.deepEqual(normalizeAnswer(full), full);
});

test("problems are told apart from prices", () => {
  for (const k of ["unavailable", "not_offered", "out_of_credits"]) assert.equal(isProblem(k), true);
  for (const k of ["answer", "parlay", "ask"]) assert.equal(isProblem(k), false);
  assert.deepEqual(["Over", "Under", "Yes", "No", "Browns", null].map(oppositeSide),
                   ["Under", "Over", "No", "Yes", null, null]);
});

test("the assistant gets the last three answered exchanges, as words", () => {
  const said = (n: number): Turn => turn(n, { ...answerAt("2026-11-20T21:42:00Z"), message: `reply ${n}` });
  const turns = [said(1), turn(2), said(3), said(4), said(5)];  // turn 2 never got an answer
  assert.deepEqual(historyFor(turns).map((h) => `${h.role}: ${h.text}`), [
    "user: question 3", "assistant: reply 3", "user: question 4", "assistant: reply 4",
    "user: question 5", "assistant: reply 5"]);
  assert.deepEqual(historyFor([]), []);
  assert.deepEqual(historyFor([turn(1)]), []);
  const long = { ...said(1), text: "q".repeat(900) };
  assert.equal(historyFor([long])[0]?.text.length, 500);
  assert.ok(historyFor(turns).length <= 6);              // the backend's limit
});

test("an answer from the assistant is marked, and none is by default", () => {
  const a = normalizeAnswer({ message: "m", credits: credits() });
  assert.equal(a.ai, false);
  assert.equal(normalizeAnswer({ message: "m", credits: credits(), ai: true, kind: "chat" }).ai, true);
});
