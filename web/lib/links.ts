import type { Choice, Focus } from "./types";

/** The permalink for a player's or a team's cards, carrying what the person asked about. */
export function resultHref(kind: "player" | "team", gameId: number, id: string,
                           focus?: Partial<Focus>): string {
  const base = kind === "player" ? `/g/${gameId}/${id}` : `/g/${gameId}/team/${id}`;
  const q = new URLSearchParams();
  if (focus?.play) q.set("play", focus.play);
  if (focus?.market) q.set("market", focus.market);
  if (focus?.threshold) q.set("threshold", focus.threshold);
  if (focus?.yards) q.set("yards", String(focus.yards));
  if (focus?.expect) q.set("expect", "1");
  const s = q.toString();
  return s ? `${base}?${s}` : base;
}

export function choiceHref(c: Choice, focus?: Partial<Focus>): string {
  return resultHref(c.kind, c.game_id, c.id, focus);
}

const PLAY_OF_KIND: Record<string, string> = {
  touchdown: "touchdown", run: "run", catch: "catch", field_goal: "field_goal",
};

/** A "Live now" chip: the play is what they are asking about, and it is already in the feed. */
export function playHref(gameId: number, athleteId: string, kind: string,
                         yards: number | null): string {
  return resultHref("player", gameId, athleteId, {
    play: PLAY_OF_KIND[kind] ?? null, yards: yards ?? null, expect: false,
  });
}
