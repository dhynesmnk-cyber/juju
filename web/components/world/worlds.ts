// Which world a page shows, and how the Jujus feel in it (docs/GOALS.md, M3 "worlds").
// The world follows the first card's outcome: that is the bet the person asked about.
import type { Outcome } from "@/lib/types";

export const WORLDS = ["pre", "live", "wait", "cash", "won", "lost", "calm", "static"] as const;
export type World = (typeof WORLDS)[number];
export type Mood = "sleepy" | "cheer" | "look" | "joy" | "content" | "sad" | "calm" | "fix";

const OF_OUTCOME: Record<Outcome, World> = {
  pregame: "pre", live: "live", waiting_for_feed: "wait", locked: "cash", won: "won",
  gone: "lost", lost: "lost", push: "calm", void: "calm", no_stat_line: "calm",
  untracked: "live", unavailable: "static",
};

export function worldFor(outcome: Outcome | undefined, hasPrice: boolean): World {
  if (!outcome || !hasPrice) return "calm";
  return OF_OUTCOME[outcome];
}

export const MOOD: Record<World, Mood> = {
  pre: "sleepy", live: "cheer", wait: "look", cash: "joy", won: "content", lost: "sad",
  calm: "calm", static: "fix",
};

/** The static world: shown before the shader loads, and instead of it when WebGL is off,
 *  the phone is struggling, or NEXT_PUBLIC_WORLDS=off. */
export const FALLBACK: Record<World, string> = {
  pre: "linear-gradient(#0d1330, #03050d)",
  live: "linear-gradient(#050a18, #0a2016)",
  wait: "linear-gradient(#050a18, #0b1a2a)",
  cash: "radial-gradient(circle at 50% 70%, #6b3a06, #140805)",
  won: "linear-gradient(#2a1d3c, #8a5320)",
  lost: "linear-gradient(#0b0b0c, #1a1b1e)",
  calm: "linear-gradient(#141722, #0b0d13)",
  static: "linear-gradient(#1b1d22, #121316)",
};

export const worldsEnabled = process.env.NEXT_PUBLIC_WORLDS !== "off";
