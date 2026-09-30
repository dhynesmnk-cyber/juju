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
  disclaimer: string;
}

export interface TeamView {
  game: GameView;
  team: { espn_id: string; name: string; abbr: string | null };
  cards: Card[];
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

export interface LiveGame extends GameView {
  plays: LivePlay[];
}

export interface LiveResponse {
  games: LiveGame[];
  next_kickoff: { label: string; commence_time: string } | null;
}

export interface Choice {
  kind: "player" | "team";
  game_id: number;
  id: string;
  name: string;
  detail: string;
}

export interface Focus {
  play: string | null;
  market: string | null;
  threshold: string | null;
  expect: boolean;
  yards: number | null;
}

export interface LookupResponse {
  kind: "player" | "team" | "choices" | "none";
  path: string;
  game_id: number | null;
  id: string | null;
  choices: Choice[];
  message: string | null;
  focus: Focus;
}
