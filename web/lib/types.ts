// The shapes the internal API returns (backend/juju/api). Money and lines arrive as strings so
// no cent is lost to floating point.

export type Outcome =
  | "pregame" | "waiting_for_feed" | "live" | "locked" | "gone" | "won" | "lost" | "push"
  | "void" | "no_stat_line" | "untracked" | "unavailable";

export type Freshness = "ok" | "amber" | "red";

export interface Price {
  american: number;
  book: string;
  book_name: string;
  point: string | null;
  observed_at: string;
  timing: "on_time" | "early";
  minutes_before_kickoff: number;
  source: "live" | "historical";
  market_last_update: string | null;
  provenance: string;
}

export interface Card {
  key: string;
  label: string;
  bet: string;
  line: string | null;
  outcome: Outcome;
  outcome_text: string;
  current: string | null;
  needed: string | null;
  headline: string;
  price: Price | null;
  no_price: string | null;
  returns: string | null;
  profit: string | null;
  fair_returns: string | null;
  fair_note: string | null;
  others: { book_name: string; american: number; point: string | null; timing: string }[];
  touched: boolean;
  named: boolean;
  alternate: boolean;
  verified: boolean; // the settled number matches the official stats (nflverse)
  // The play that took it past the line. `exact` false: the live feed's "on or around", and
  // `text` may be null (only the game clock is known).
  decided_by: {
    text: string | null; period: number | null; clock: string | null; exact: boolean;
  } | null;
  notes: string[];
}

export interface Side {
  name: string;
  abbr: string | null;
  score: number | null;
  espn_id: string | null;
}

export interface GameView {
  id: number;
  label: string;
  home: Side;
  away: Side;
  status: string;
  status_text: string;
  commence_time: string;
  freshness: Freshness;
  updated_seconds_ago: number | null;
}

export interface PlayerView {
  game: GameView;
  player: { id: string; name: string; team_abbr: string | null; position: string | null };
  cards: Card[];
  waiting_for_feed: boolean;
  no_price?: string | null;  // why there are no cards at all
  the_play?: ThePlay | null;  // the play tapped, when the page was opened from one
  disclaimer: string;
}

export interface ThePlay {
  id: number;
  label: string;
  text: string;
  period: number | null;
  clock: string | null;
  yards: number | null;
}

export interface TeamView {
  game: GameView;
  team: { espn_id: string; name: string; abbr: string | null };
  cards: Card[];
  the_play?: ThePlay | null;
  disclaimer: string;
}

export interface LivePlay {
  id: number;
  kind: string;
  text: string;
  yards: number | null;
  period: number | null;
  clock: string | null;
  wallclock: string | null;
  athlete_id: string | null;
  team_espn_id: string | null;
  scoring: boolean;
  label: string;
}

/** One game's page (/api/game): every notable play, newest first, and the players Juju has a
 *  price for. No prices in it. */
export interface GamePlay extends LivePlay {
  passer_id: string | null;
}

export interface GamePlayer {
  id: string;
  name: string;
  team_espn_id: string | null;
  position: string | null;
}

export interface GamePageView extends GameView {
  plays: GamePlay[];
  players: GamePlayer[];
}

export interface LiveGame extends GameView {
  plays: LivePlay[];  // the latest notable plays, for games in play only
}

export interface LiveResponse {
  games: LiveGame[];
  next_kickoff: { label: string; commence_time: string } | null;
}

export interface Choice {
  kind: "player" | "team";  // the page it opens
  game_id: number;
  id: string;
  name: string;    // a play's label, for one of the latest plays
  detail: string;
  play_id?: number | null;  // set for one of the latest plays
  play?: string | null;
  yards?: number | null;
}

export interface Focus {
  play: string | null;
  market: string | null;
  threshold: string | null;
  expect: boolean;
  yards: number | null;
}

export interface LookupResponse {
  kind: "player" | "team" | "parlay" | "choices" | "plays" | "none";
  path: string;
  game_id: number | null;
  id: string | null;
  choices: Choice[];
  message: string | null;
  legs: string | null;  // a parlay's legs, as its URL writes them
  focus: Focus;
}

// A same-game parlay (M4). Each leg's price is the one its own card shows, at the parlay's book.
export interface ParlayLeg {
  key: string;
  kind: "player" | "team";
  id: string;
  who: string;
  label: string;
  bet: string;
  line: string | null;
  outcome: Outcome;
  outcome_text: string;
  current: string | null;
  needed: string | null;
  price: Price | null;
  no_price: string | null;
}

export interface ParlayView {
  game: GameView;
  legs: ParlayLeg[];
  outcome: Outcome;
  outcome_text: string;
  headline: string;
  returns: string | null;
  fair_returns: string | null;
  book_name: string | null;
  legs_counted: number;
  notes: string[];
  disclaimer: string;
}

// The owners' own section (/api/me): the bet pages each opened, worked out again when read.
// A recap's price is the one its page shows, with no other book (docs/licensing.md).
export interface RecapPrice {
  american: number;
  book_name: string;
  minutes_before_kickoff: number;
  timing: "on_time" | "early";
}

export interface RecapBet {
  key: string;
  label: string;
  bet: string;
  line: string | null;
  outcome: Outcome;
  outcome_text: string;
  headline: string;
  current: string | null;
  needed: string | null;
  price: RecapPrice | null;
  no_price: string | null;
  verified: boolean;
  decided_by: Card["decided_by"];
  notes: string[];
  back: string | null;  // what $10 came back as, once settled
}

export interface RecapParlay {
  outcome: Outcome;
  outcome_text: string;
  headline: string;
  book_name: string | null;
  priced: boolean;
  back: string | null;
  legs: { key: string; kind: "player" | "team"; id: string; who: string; bet: string;
          outcome: Outcome; outcome_text: string; price: RecapPrice | null;
          no_price: string | null }[];
}

/** A bet page as it was opened: what /api/me/lookups saves. */
export interface Remembered {
  kind: "player" | "team" | "parlay";
  game_id: number;
  target: string;  // an athlete id, a team id, or a parlay's legs
  play?: string | null;
  market?: string | null;
  threshold?: string | null;
  yards?: number | null;
  expect?: boolean;
  play_id?: number | null;
}

export interface RecapLookup extends Remembered {
  id: number;
  times: number;
  first_seen_at: string;
  last_seen_at: string;
  who: string | null;
  asked: string | null;
  bets: RecapBet[];
  parlay: RecapParlay | null;
  unpriced: number;
  no_price: string | null;
}

export interface RecapTotals {
  bets: number;
  won: number;
  lost: number;
  stake_back: number;
  open: number;
  unpriced: number;
  staked: string;
  back: string;
}

export type RecapState = "pregame" | "in_play" | "final" | "checked" | "off";

export interface Recap {
  game: GameView;
  state: RecapState;
  totals: RecapTotals;
  lookups: RecapLookup[];
}

export interface RecapGame {
  game: GameView;
  state: RecapState;
  lookups: number;
  last_seen_at: string;
  totals?: RecapTotals;
}

export interface History {
  kind: "player" | "team";
  id: string;
  name: string | null;
  games: { game: GameView; state: RecapState; bets: RecapBet[]; unpriced: number;
           lookup_ids: number[] }[];
  totals: RecapTotals;
}
