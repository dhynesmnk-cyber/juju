// The live chat's shapes and small rules (docs/plans/live-chat.md): what /api/live/* answers,
// how a thread is kept on the device, when an answer has gone stale, and the words for credits.
// No "@/" imports: `npm test` runs the lib tests with Node's own test runner.
import { american } from "./format.ts";

export interface ChatGame {
  id: string;
  label: string;
  home: string;
  away: string;
  commence_time: string;
  in_play: boolean;
}

export interface Credits {
  remaining: number | null;   // The Odds API's own count; null until it has said
  state: "unknown" | "ok" | "low" | "out";
  low_at: number;
  spent: number;              // what live questions have spent since the API started
  note: string | null;
}

export interface PriceView {
  book: string;
  book_name: string;
  american: number;
  returns: string;            // what $10 returns, with the stake, from the API's own maths
  last_update: string | null;
}

export interface PickView {
  point: string | null;
  bet: string;
  book: PriceView;
  others: PriceView[];
  opposite: PriceView | null;
}

export interface LegView {
  market: string;
  label: string;
  subject: string | null;
  side: string | null;
  asked: string | null;
  exact: boolean;
  available: boolean;
  missing: string | null;
  stands: string | null;
  picks: PickView[];
}

export interface ParlayView {
  book: string | null;
  book_name: string | null;
  returns: string;
  notes: string[];
  legs: PriceView[];
}

export interface LiveAnswer {
  kind: string;               // answer, parlay, chat, refused, unavailable, ask, not_offered, ...
  message: string;
  sentences: string[];
  legs: LegView[];
  parlay: ParlayView | null;
  choices: string[];
  as_of: string | null;
  cached: boolean;
  cost: number;               // credits this question spent
  credits: Credits;
  disclaimer: string;
  ai: boolean;                // the words are the assistant's (the numbers never are)
}

export interface PlanCost {
  low: number;
  high: number;
  cached: number;
  text: string;               // "This will cost 1 to 2 credits."
}

export interface MarketItem {
  key: string;
  label: string;
  scope: string;
  alternate: boolean;
}

export interface Turn {
  id: string;
  at: number;                 // when it was asked, ms
  text: string;
  answer: LiveAnswer | null;
  error: string | null;       // why there is no answer
}

export interface HistoryTurn {
  role: "user" | "assistant";
  text: string;
}

const HISTORY_EXCHANGES = 3;
const HISTORY_TEXT = 500;

/** The last few exchanges, as the assistant reads them for context ("and the under?"): what was
 *  asked and what Juju said. Only exchanges that were answered. The backend never trusts the
 *  numbers in it, so this is words only (core/assistant.py). */
export function historyFor(turns: Turn[]): HistoryTurn[] {
  const out: HistoryTurn[] = [];
  for (const t of turns.filter((x) => x.answer).slice(-HISTORY_EXCHANGES)) {
    out.push({ role: "user", text: t.text.slice(0, HISTORY_TEXT) });
    out.push({ role: "assistant", text: (t.answer?.message ?? "").slice(0, HISTORY_TEXT) });
  }
  return out;
}

/** Odds are never cached for longer than this (backend/juju/ingest/live_odds.py): past it, an
 *  answer on screen is an old price. */
export const STALE_AFTER_MS = 60_000;
export const MAX_TURNS = 40;

/** What the API answered, with every field filled in. A failure it can explain (the Odds API
 *  down, no credits) has a kind, a message and the credits, and none of the rest. */
export function normalizeAnswer(raw: Partial<LiveAnswer> & { message: string; credits: Credits }
                                ): LiveAnswer {
  return {
    kind: raw.kind ?? "answer", message: raw.message, sentences: raw.sentences ?? [raw.message],
    legs: raw.legs ?? [], parlay: raw.parlay ?? null, choices: raw.choices ?? [],
    as_of: raw.as_of ?? null, cached: raw.cached ?? true, cost: raw.cost ?? 0,
    credits: raw.credits, disclaimer: raw.disclaimer ?? "", ai: raw.ai ?? false,
  };
}

const OPPOSITE: Record<string, string> = { Over: "Under", Under: "Over", Yes: "No", No: "Yes" };
export const oppositeSide = (side: string | null): string | null =>
  side ? (OPPOSITE[side] ?? null) : null;

/** A reply that is a problem (the market isn't posted, no credits) rather than a price. */
export const isProblem = (kind: string): boolean =>
  ["unavailable", "not_offered", "out_of_credits"].includes(kind);

export function ageMs(asOf: string | null, nowMs: number): number | null {
  if (!asOf) return null;
  const t = Date.parse(asOf);
  return Number.isNaN(t) ? null : Math.max(0, nowMs - t);
}

/** A price on the screen that is a minute old or older may have moved. */
export function isStale(answer: LiveAnswer | null, nowMs: number): boolean {
  const age = answer ? ageMs(answer.as_of, nowMs) : null;
  return age !== null && age >= STALE_AFTER_MS;
}

export function clockTime(iso: string, tz?: string): string {
  return new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", timeZone: tz })
    .format(new Date(iso));
}

export function priceText(p: Pick<PriceView, "american">): string {
  return american(p.american);
}

/** "$10 → $18.00": the API's number, never worked out here. */
export function returnsText(p: Pick<PriceView, "returns">): string {
  return `$10 → $${Number(p.returns).toLocaleString("en-US", {
    minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function creditsText(c: Credits | null): string {
  if (!c || c.state === "unknown" || c.remaining === null) {
    return "Odds credits: shown after your first question";
  }
  if (c.state === "out") return "Out of odds credits";
  const left = `${c.remaining.toLocaleString("en-US")} odds credits left`;
  return c.state === "low" ? `${left}: running low` : left;
}

/** What to say when the question never got an answer. */
export function askErrorText(status: number | null): string {
  switch (status) {
    case 401: return "Your passcode session ended. Reload the page and enter it again.";
    case 404: return "That game is no longer on the live schedule, or live odds are off.";
    case 422: return "Juju couldn't read that. Try asking it another way.";
    case 503: return "Live odds aren't available right now. Nothing was shown, and no price was made up.";
    default: return "Juju couldn't reach live odds. Try again in a moment.";
  }
}

// --- The thread, kept on this device per game --------------------------------------------------

export type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem" | "key" | "length">;
const PREFIX = "juju-live:";

export const threadKey = (gameId: string) => `${PREFIX}${gameId}`;

function validTurn(t: unknown): t is Turn {
  if (typeof t !== "object" || t === null) return false;
  const x = t as Record<string, unknown>;
  return typeof x.id === "string" && typeof x.at === "number" && typeof x.text === "string"
    && (x.answer === null || typeof x.answer === "object")
    && (x.error === null || typeof x.error === "string");
}

/** The saved thread, or none: a missing, blocked or damaged store never breaks the page. */
export function loadThread(store: StorageLike | null, gameId: string): Turn[] {
  try {
    const raw = store?.getItem(threadKey(gameId));
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter(validTurn).slice(-MAX_TURNS) : [];
  } catch {
    return [];
  }
}

export function saveThread(store: StorageLike | null, gameId: string, turns: Turn[]): void {
  try {
    const kept = turns.slice(-MAX_TURNS);
    if (kept.length === 0) store?.removeItem(threadKey(gameId));
    else store?.setItem(threadKey(gameId), JSON.stringify(kept));
  } catch {
    // a full or blocked store: the thread lives on the page until it is closed
  }
}

/** Threads of games that have left the schedule are dropped (the game is over). Only call with
 *  a schedule that loaded and has games in it. */
export function forgetEndedThreads(store: StorageLike | null, onSchedule: string[]): void {
  try {
    if (!store) return;
    const stale: string[] = [];
    for (let i = 0; i < store.length; i++) {
      const key = store.key(i);
      if (key?.startsWith(PREFIX) && !onSchedule.includes(key.slice(PREFIX.length))) {
        stale.push(key);
      }
    }
    for (const key of stale) store.removeItem(key);
  } catch {
    // nothing to tidy
  }
}

export function newTurnId(nowMs: number, random: () => number = Math.random): string {
  return `${nowMs.toString(36)}-${random().toString(36).slice(2, 8)}`;
}
