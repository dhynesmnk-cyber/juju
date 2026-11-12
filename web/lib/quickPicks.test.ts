// Quick picks are built from the game's own no-price lists: these hold them to that.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { MARKET_LABEL, betText, playerLeg, teamLeg } from "./markets.ts";
import { MOST_PICKS, quickPicks } from "./quickPicks.ts";
import type { GamePageView, GamePlay, GamePlayer } from "./types.ts";

// The recorded PHI @ CHI game as /api/game returns it: who has a price, and its notable plays,
// newest first. No prices in it (backend/tests/db/test_api.py holds the endpoint to that).
const PHI = "21", CHI = "3";
const player = (id: string, name: string, team: string, position: string): GamePlayer =>
  ({ id, name, team_espn_id: team, position });
const PLAYERS = [
  player("4040715", "Jalen Hurts", PHI, "QB"), player("3929630", "Saquon Barkley", PHI, "RB"),
  player("4429013", "Tank Bigsby", PHI, "RB"), player("15835", "Zach Ertz", PHI, "TE"),
  player("4241478", "DeVonta Smith", PHI, "WR"), player("4870795", "Makai Lemon", PHI, "WR"),
  player("15168", "Case Keenum", CHI, "QB"), player("4259545", "D'Andre Swift", CHI, "RB"),
  player("4608686", "Kyle Monangai", CHI, "RB"), player("4258595", "Cole Kmet", CHI, "TE"),
  player("2973405", "Kalif Raymond", CHI, "WR"), player("4685278", "Luther Burden III", CHI, "WR"),
  player("4431299", "Rome Odunze", CHI, "WR"),
];
let nextId = 100;
const play = (kind: string, athlete: string, team: string, yards: number,
              passer: string | null = null): GamePlay => ({
  id: nextId--, kind, label: "", text: "", yards, period: 1, clock: "0:00", wallclock: null,
  athlete_id: athlete, passer_id: passer, team_espn_id: team, scoring: kind !== "run" && kind !== "catch",
});
const PLAYS = [
  play("run", "4040715", PHI, 20),                          // J. Hurts 20-yd run
  play("touchdown", "15168", CHI, 1),                       // C. Keenum 1-yd TD run
  play("def_interception", "3121544", CHI, 0, "4040715"),   // thrown by Hurts
  play("touchdown", "2973405", CHI, 41, "15168"),           // K. Raymond 41-yd TD catch
  play("field_goal", "17427", CHI, 48),
  play("touchdown", "4040715", PHI, 1),                     // J. Hurts 1-yd TD run
  play("catch", "4241478", PHI, 30, "4040715"),             // D. Smith 30-yd catch
  play("field_goal", "17427", CHI, 29),
  play("catch", "4431299", CHI, 22, "15168"),               // R. Odunze 22-yd catch
  play("catch", "4685278", CHI, 25, "15168"),               // L. Burden III 25-yd catch
  play("touchdown", "4685278", CHI, 8, "15168"),            // L. Burden III 8-yd TD catch
];
const game = (over: Partial<GamePageView> = {}): GamePageView => ({
  id: 1, label: "PHI @ CHI", status: "in_progress", status_text: "Q4 5:12",
  commence_time: "2026-09-29T00:15:00Z", freshness: "ok", updated_seconds_ago: 5,
  away: { name: "Philadelphia Eagles", abbr: "PHI", score: 7, espn_id: PHI },
  home: { name: "Chicago Bears", abbr: "CHI", score: 27, espn_id: CHI },
  players: PLAYERS, plays: PLAYS, ...over,
});

const keys = (legs: { key: string }[]) => legs.map((l) => l.key);

test("the recorded game's quick picks, from its players and plays alone", () => {
  const picks = quickPicks(game());
  assert.deepEqual(picks.map((p) => p.title),
                   ["The quarterbacks", "The big plays", "PHI stack", "CHI stack"]);
  assert.deepEqual(keys(picks[0].legs), ["p-4040715-player_pass_yds", "p-15168-player_pass_yds"]);
  // Newest first, a runner's rush yards and a receiver's receiving yards, four at most.
  assert.deepEqual(keys(picks[1].legs), [
    "p-4040715-player_rush_yds", "p-2973405-player_reception_yds",
    "p-4241478-player_reception_yds", "p-4431299-player_reception_yds"]);
  assert.deepEqual(keys(picks[2].legs),
                   ["p-4040715-player_pass_yds", "p-4241478-player_reception_yds", "t-21-spreads"]);
  // Burden caught two of Keenum's notable passes, Raymond and Odunze one each.
  assert.deepEqual(keys(picks[3].legs),
                   ["p-15168-player_pass_yds", "p-4685278-player_reception_yds", "t-3-spreads"]);
  assert.equal(picks[2].about, "Jalen Hurts to DeVonta Smith, and PHI on the spread.");
});

test("a pick carries names and bets in words, and no price", () => {
  for (const pick of quickPicks(game())) {
    assert.ok(pick.legs.length >= 2 && pick.legs.length <= 6, pick.title);
    assert.equal(new Set(keys(pick.legs)).size, pick.legs.length, pick.title);
    for (const leg of pick.legs) {
      assert.deepEqual(Object.keys(leg).sort(), ["bet", "key", "who"]);
      // A line is part of a price: none of the words may hold a number, a "$" or odds.
      assert.doesNotMatch(`${leg.bet} ${pick.about}`.replace(/\b20-yard\b/, ""), /[$\d+]|\bat\b/);
    }
  }
  assert.equal(betText("player_rush_yds"), "Over the rush yards line");
  assert.equal(betText("spreads", "CHI"), "CHI on the spread");
});

test("every leg is one the parlay endpoint reads", () => {
  // backend/juju/core/parlay.py, _LEG: the same pattern.
  const LEG = /^(?:p-\d{1,20}-[a-z0-9_]{1,60}(?:-\d{1,3}(?:\.5)?)?|t-\d{1,10}-[a-z_]{1,20})$/;
  for (const pick of quickPicks(game())) for (const k of keys(pick.legs)) assert.match(k, LEG);
  assert.equal(playerLeg("3929630", "player_rush_yds"), "p-3929630-player_rush_yds");
  assert.equal(teamLeg("3", "spreads"), "t-3-spreads");
});

test("before kickoff there are no plays to go on: the quarterbacks, and a stack each", () => {
  const picks = quickPicks(game({ plays: [], status: "scheduled" }));
  assert.deepEqual(picks.map((p) => p.title), ["The quarterbacks", "PHI stack", "CHI stack"]);
  assert.deepEqual(keys(picks[1].legs), ["p-4040715-player_pass_yds", "t-21-spreads"]);
  assert.equal(picks[1].about, "Jalen Hurts, and PHI on the spread.");
});

test("a pick is never a guess: with two priced quarterbacks and no pass seen, none is named", () => {
  const two = [...PLAYERS, player("999", "Tanner McKee", PHI, "QB")];
  const picks = quickPicks(game({ players: two, plays: [] }));
  assert.deepEqual(picks.map((p) => p.title), ["CHI stack"]);
  // Once the plays show who is throwing, he is the one.
  const seen = quickPicks(game({ players: two }));
  assert.ok(seen.some((p) => p.title === "The quarterbacks"));
  assert.deepEqual(keys(seen[0].legs), ["p-4040715-player_pass_yds", "p-15168-player_pass_yds"]);
});

test("the running backs, once the plays say who carried it", () => {
  // Each team has two priced backs and the recorded plays show neither carrying: no pick.
  assert.ok(!quickPicks(game()).some((p) => p.id === "ground"));
  const runs = [play("run", "3929630", PHI, 22), play("touchdown", "4259545", CHI, 3)];
  const picks = quickPicks(game({ plays: runs }));
  const ground = picks.find((p) => p.id === "ground");
  assert.deepEqual(keys(ground?.legs ?? []), ["p-3929630-player_rush_yds", "p-4259545-player_rush_yds"]);
  assert.ok(picks.length <= MOST_PICKS);
});

test("no priced players, no picks; and a play by someone unpriced is left out", () => {
  assert.deepEqual(quickPicks(game({ players: [] })), []);
  const kicker = quickPicks(game({ plays: [play("run", "17427", CHI, 40), ...PLAYS] }));
  assert.ok(!kicker.flatMap((p) => keys(p.legs)).some((k) => k.includes("17427")));
});

test("the labels are the backend catalogue's", () => {
  const catalog = readFileSync(
    new URL("../../backend/juju/core/markets.py", import.meta.url), "utf8");
  for (const [key, label] of Object.entries(MARKET_LABEL)) {
    assert.match(catalog, new RegExp(`Market\\("${key}", "${label}",`), key);
  }
});
