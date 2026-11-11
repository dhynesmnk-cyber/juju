// Quick picks: a few ready-made same-game parlays for one game, put together in the browser
// from what its page already has: the players Juju holds a price for (their names, teams and
// positions) and the game's notable plays. No price is read to build one, and none is shown
// until someone opens it: then it is an ordinary parlay of the legs they chose, from the
// parlay's own endpoint (docs/licensing.md). No "@/" imports: `npm test` runs the lib tests
// with Node's own test runner.
import { betText, playerLeg, teamLeg, type MarketKey } from "./markets.ts";
import type { TrayLeg } from "./parlayTray.ts";
import type { GamePageView, GamePlay, GamePlayer, Side } from "./types.ts";

export interface QuickPick {
  id: string;
  title: string;
  about: string;   // what it is, in a line: never a tip
  legs: TrayLeg[];
}

export const MOST_PICKS = 4;
const BIG_PLAY = 20;  // the feed's own line for a notable gain
const MOST_LEGS = 4;  // of a pick: short enough to read on a phone

type Team = Side & { espn_id: string };

function leg(p: GamePlayer, market: MarketKey): TrayLeg {
  return { key: playerLeg(p.id, market), who: p.name, bet: betText(market) };
}

/** Was the ball thrown? A catch, or a touchdown with a passer named. */
function thrown(p: GamePlay): boolean {
  return p.kind === "catch" || (p.kind === "touchdown" && !!p.passer_id);
}

function carried(p: GamePlay): boolean {
  return p.kind === "run" || (p.kind === "touchdown" && !p.passer_id);
}

/** Whoever of `ids` has the most plays that `counts`, the latest play breaking a tie (plays
 *  come newest first); null if none of them has one. */
function busiest(plays: GamePlay[], ids: Set<string>, who: (p: GamePlay) => string | null,
                 counts: (p: GamePlay) => boolean): string | null {
  const tally = new Map<string, number>();
  for (const p of plays) {
    const id = who(p);
    if (id && ids.has(id) && counts(p)) tally.set(id, (tally.get(id) ?? 0) + 1);
  }
  let best: string | null = null;
  for (const [id, n] of tally) if (best === null || n > (tally.get(best) ?? 0)) best = id;
  return best;
}

/** The one of a team's players at `position` this game points to: the only one with a price,
 *  or the one its plays show doing the job. Null when it can't tell: a pick is never a guess. */
function starter(g: GamePageView, team: Team, position: string,
                 who: (p: GamePlay) => string | null,
                 counts: (p: GamePlay) => boolean): GamePlayer | null {
  const at = g.players.filter((p) => p.team_espn_id === team.espn_id && p.position === position);
  if (at.length === 1) return at[0];
  const id = busiest(g.plays, new Set(at.map((p) => p.id)), who, counts);
  return at.find((p) => p.id === id) ?? null;
}

const quarterback = (g: GamePageView, team: Team) =>
  starter(g, team, "QB", (p) => p.passer_id, () => true);
const runner = (g: GamePageView, team: Team) =>
  starter(g, team, "RB", (p) => p.athlete_id, carried);

/** The receiver a quarterback found most in this game's notable plays. */
function target(g: GamePageView, qb: GamePlayer): GamePlayer | null {
  const mates = new Set(g.players.filter((p) => p.team_espn_id === qb.team_espn_id
                                               && p.id !== qb.id).map((p) => p.id));
  const id = busiest(g.plays, mates, (p) => (p.passer_id === qb.id ? p.athlete_id : null), thrown);
  return g.players.find((p) => p.id === id) ?? null;
}

/** The quick picks for a game, most telling first, at most MOST_PICKS. Each has 2 to 4 legs;
 *  a game with no priced players has none. */
export function quickPicks(g: GamePageView): QuickPick[] {
  const teams = [g.away, g.home].filter((s): s is Team => !!s.espn_id);
  if (teams.length !== 2 || g.players.length === 0) return [];
  const priced = new Map(g.players.map((p) => [p.id, p]));
  const picks: QuickPick[] = [];
  const qbs = teams.map((t) => quarterback(g, t));

  if (qbs[0] && qbs[1]) {
    picks.push({
      id: "quarterbacks", title: "The quarterbacks",
      about: `${qbs[0].name} and ${qbs[1].name}, each over his pass yards line.`,
      legs: [leg(qbs[0], "player_pass_yds"), leg(qbs[1], "player_pass_yds")],
    });
  }

  const big: TrayLeg[] = [];
  for (const p of g.plays) {
    const player = p.athlete_id ? priced.get(p.athlete_id) : undefined;
    if (!player || (p.yards ?? 0) < BIG_PLAY || !(thrown(p) || carried(p))) continue;
    const one = leg(player, thrown(p) ? "player_reception_yds" : "player_rush_yds");
    if (big.length < MOST_LEGS && !big.some((l) => l.key === one.key)) big.push(one);
  }
  if (big.length >= 2) {
    picks.push({
      id: "big-plays", title: "The big plays",
      about: `Whoever made this game's ${BIG_PLAY}-yard plays, each over his yards line.`,
      legs: big,
    });
  }

  teams.forEach((team, i) => {
    const qb = qbs[i];
    if (!qb) return;
    const side = team.abbr ?? team.name;
    const to = target(g, qb);
    const legs = [leg(qb, "player_pass_yds"), ...(to ? [leg(to, "player_reception_yds")] : []),
                  { key: teamLeg(team.espn_id, "spreads"), who: side,
                    bet: betText("spreads", side) }];
    picks.push({
      id: `stack-${team.espn_id}`, title: `${side} stack`,
      about: `${qb.name}${to ? ` to ${to.name}` : ""}, and ${side} on the spread.`,
      legs,
    });
  });

  const backs = teams.map((t) => runner(g, t));
  if (backs[0] && backs[1]) {
    picks.push({
      id: "ground", title: "On the ground",
      about: `${backs[0].name} and ${backs[1].name}, each over his rush yards line.`,
      legs: [leg(backs[0], "player_rush_yds"), leg(backs[1], "player_rush_yds")],
    });
  }
  return picks.slice(0, MOST_PICKS);
}
