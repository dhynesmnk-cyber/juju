// The parlay tray: the legs someone has picked for one game, kept in their own browser only
// (a per-viewer convenience: nothing is sent anywhere until they open the parlay).
import type { Card } from "./types";

export interface TrayLeg { key: string; who: string; bet: string }

export const MAX_LEGS = 6;  // backend/juju/core/parlay.py
export const TRAY_EVENT = "juju-parlay";
const TEAM_MARKETS = new Set(["h2h", "spreads", "totals", "team_totals"]);

const storageKey = (game: number) => `juju-parlay-${game}`;

export function readTray(game: number): TrayLeg[] {
  try {
    const legs = JSON.parse(window.localStorage.getItem(storageKey(game)) ?? "[]");
    return Array.isArray(legs)
      ? legs.filter((l) => typeof l?.key === "string" && typeof l?.bet === "string")
        .slice(0, MAX_LEGS)
      : [];
  } catch {
    return [];
  }
}

function write(game: number, legs: TrayLeg[]): void {
  try {
    if (legs.length) window.localStorage.setItem(storageKey(game), JSON.stringify(legs));
    else window.localStorage.removeItem(storageKey(game));
  } catch {
    // Storage blocked (a private window): the tray just doesn't persist.
  }
  window.dispatchEvent(new CustomEvent(TRAY_EVENT, { detail: { game, legs } }));
}

/** Add the leg, or take it out if it is already in. The tray holds at most MAX_LEGS. */
export function toggleLeg(game: number, leg: TrayLeg): TrayLeg[] {
  const legs = readTray(game);
  const next = legs.some((l) => l.key === leg.key) ? legs.filter((l) => l.key !== leg.key)
    : legs.length < MAX_LEGS ? [...legs, leg] : legs;
  write(game, next);
  return next;
}

export function clearTray(game: number): void {
  write(game, []);
}

/** A card's leg, as the parlay URL writes it; null for a card that can't be a leg. */
export function legKey(kind: "player" | "team", id: string, card: Card): string | null {
  if (!card.price) return null;
  if (kind === "team") return TEAM_MARKETS.has(card.key) ? `t-${id}-${card.key}` : null;
  return card.alternate && card.line ? `p-${id}-${card.key}-${card.line}` : `p-${id}-${card.key}`;
}

export function parlayHref(game: number, legs: string): string {
  return `/g/${game}/parlay?legs=${encodeURIComponent(legs)}`;
}

/** Where a leg's own card is: its player's or team's page, with that card first. */
export function legCardHref(game: number, legKey: string): string {
  const [kind, id, market, threshold] = legKey.split("-");
  const q = new URLSearchParams({ card: market });
  if (threshold) q.set("threshold", threshold);
  return kind === "t" ? `/g/${game}/team/${id}?${q}` : `/g/${game}/${id}?${q}`;
}
