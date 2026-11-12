// The part of the market catalogue (backend/juju/core/markets.py) the site names by itself:
// the bets a quick pick is made of. Keys and labels only, never a price; lib/quickPicks.test.ts
// holds each label to the backend's. No "@/" imports: `npm test` runs the lib tests with
// Node's own test runner.
export const MARKET_LABEL = {
  player_pass_yds: "Pass yards",
  player_rush_yds: "Rush yards",
  player_reception_yds: "Receiving yards",
  spreads: "Spread",
} as const;

export type MarketKey = keyof typeof MARKET_LABEL;

/** A leg as the parlay URL writes it (backend/juju/core/parlay.py). */
export function playerLeg(athleteId: string, market: MarketKey): string {
  return `p-${athleteId}-${market}`;
}

export function teamLeg(teamId: string, market: MarketKey): string {
  return `t-${teamId}-${market}`;
}

/** A bet in words before its price is known, as the backend words one with no line. */
export function betText(market: MarketKey, side?: string): string {
  if (market === "spreads") return `${side} on the spread`;
  return `Over the ${MARKET_LABEL[market].toLowerCase()} line`;
}
