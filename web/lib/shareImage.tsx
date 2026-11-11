// The share images (Open Graph, 1200x630): one card, or one parlay, as a ticket in its world
// with the Jujus. Built with next/og from the same view the page shows, so every number on it
// is the page's own. No font is fetched: next/og's built-in font keeps the image working
// without the network. Colours are lib/tokens.ts's.
import { ImageResponse } from "next/og";
import { stillJujuSvg } from "@/components/world/Jujus";
import { MOOD, type World, worldFor } from "@/components/world/worlds";
import { american, quarter } from "@/lib/format";
import { color, worldGradient } from "@/lib/tokens";
import type { Card, GameView, Outcome, ParlayView } from "@/lib/types";

export const SIZE = { width: 1200, height: 630 };
const SETTLED = new Set<Outcome>(["won", "lost", "push", "void", "no_stat_line"]);
const CASHED = new Set<Outcome>(["won", "locked"]);
const LOST = new Set<Outcome>(["lost", "gone"]);

const GOOD: [string, string] = [color.gold, color.goldTint];
const BAD: [string, string] = [color.lost, color.lostTint];
const LIVE: [string, string] = [color.orangeBright, color.orangeTint];
const NEUTRAL: [string, string] = [color.muted, color.surface3];
const CHIP: Record<Outcome, [string, [string, string]]> = {  // icon, [text, background]
  won: ["✓", GOOD], locked: ["✓", GOOD], lost: ["✕", BAD], gone: ["✕", BAD],
  live: ["●", LIVE], waiting_for_feed: ["…", LIVE], pregame: ["◷", NEUTRAL],
  push: ["=", NEUTRAL], void: ["=", NEUTRAL], no_stat_line: ["–", NEUTRAL],
  untracked: ["–", NEUTRAL], unavailable: ["!", NEUTRAL],
};

// The image font has no ✓ ✕ ◷ ☑, so those icons are drawn; the rest are plain characters.
function Icon({ glyph, ink }: { glyph: string; ink: string }) {
  const line = { stroke: ink, strokeWidth: 3, fill: "none", strokeLinecap: "round" as const };
  const path = glyph === "✓" || glyph === "☑" ? <path d="M5 12.5l4.5 4.5L19 7.5" {...line} />
    : glyph === "✕" ? <path d="M6 6l12 12M18 6L6 18" {...line} />
    : glyph === "◷" ? <path d="M12 3a9 9 0 1 0 0.01 0M12 7v5l3 2" {...line} />
    : null;
  if (!path) return <div style={{ display: "flex", marginRight: 10 }}>{glyph}</div>;
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" style={{ marginRight: 10 }}>{path}</svg>
  );
}

function Chip({ outcome, text }: { outcome: Outcome; text: string }) {
  const [icon, [ink, bg]] = CHIP[outcome];
  return (
    <div style={{ display: "flex", alignItems: "center", padding: "6px 18px", borderRadius: 999,
                  background: bg, color: ink, fontWeight: 700, fontSize: 26 }}>
      <Icon glyph={icon} ink={ink} />{text}
    </div>
  );
}

function svgSrc(svg: string): string {
  return `data:image/svg+xml;base64,${Buffer.from(svg).toString("base64")}`;
}

function clip(text: string, n: number): string {
  return text.length > n ? `${text.slice(0, n - 1)}…` : text;
}

function scoreOf(game: GameView): string {
  return game.home.score !== null && game.away.score !== null
    ? `${game.away.abbr ?? game.away.name} ${game.away.score} – ${game.home.score} ${game.home.abbr ?? game.home.name}`
    : game.label;
}

/** How long the image may be cached: a settled card no longer changes. */
export function imageCache(game: GameView, outcome: Outcome | undefined): string {
  const done = game.status === "final" && (!outcome || SETTLED.has(outcome));
  return done ? "public, max-age=3600, s-maxage=86400"
    : "public, max-age=30, s-maxage=30, stale-while-revalidate=60";
}

/** The payout, as on the page: its label, the amount (the API's own string) and whether it
 *  still hangs on the bet hitting. */
function Payout({ outcome, back, returns }: {
  outcome: Outcome; back: string | null; returns: string | null;
}) {
  if (returns === null) {
    return <div style={{ display: "flex", fontSize: 44, fontWeight: 800 }}>No price on file</div>;
  }
  const open = back === null;
  const ink = CASHED.has(outcome) ? color.gold : LOST.has(outcome) ? color.lost : color.text;
  return (
    <div style={{ display: "flex", flexDirection: "column", marginTop: 14 }}>
      <div style={{ display: "flex", fontSize: 22, letterSpacing: 2, color: color.muted }}>
        {open ? "$10 WOULD RETURN" : "$10 WOULD HAVE RETURNED"}
      </div>
      <div style={{ display: "flex", alignItems: "baseline" }}>
        <div style={{ display: "flex", fontSize: 96, fontWeight: 800, letterSpacing: -2, color: ink }}>
          {`$${back ?? returns}`}
        </div>
        {open && <div style={{ display: "flex", fontSize: 34, marginLeft: 16,
                               color: color.muted }}>if it hits</div>}
      </div>
    </div>
  );
}

/** The frame both images share: the wordmark and score, a ticket, the Jujus, the disclaimer. */
function frame(world: World, game: GameView, ticket: React.ReactNode, foot: string,
               cache: string): ImageResponse {
  const mood = MOOD[world];
  return new ImageResponse(
    (
      <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column",
                    backgroundColor: color.bg, backgroundImage: worldGradient(world, true),
                    color: color.text, padding: "36px 56px", fontSize: 28 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center",
                      color: color.text }}>
          <div style={{ display: "flex", fontSize: 44, fontWeight: 800 }}>
            ju<span style={{ color: color.orange }}>ju</span>
          </div>
          <div style={{ display: "flex", padding: "6px 18px", borderRadius: 999, fontSize: 26,
                        background: color.surface1, border: `2px solid ${color.line}` }}>
            {`${scoreOf(game)} · ${game.status_text}`}
          </div>
        </div>
        <div style={{ display: "flex", flex: 1, alignItems: "center", margin: "14px 0" }}>
          <div style={{ display: "flex", flexDirection: "column", width: 850, padding: "24px 34px",
                        borderRadius: 24, background: color.surface1,
                        borderTop: `6px solid ${color.orange}` }}>
            {ticket}
          </div>
          <div style={{ display: "flex", alignItems: "flex-end", marginLeft: 24, height: 220 }}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={svgSrc(stillJujuSvg("pip", mood))} width={84} height={98} alt="" />
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={svgSrc(stillJujuSvg("bo", mood))} width={62} height={98} alt=""
                 style={{ marginLeft: 4 }} />
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={svgSrc(stillJujuSvg("tuft", mood))} width={84} height={98} alt=""
                 style={{ marginLeft: 4 }} />
          </div>
        </div>
        <div style={{ display: "flex", fontSize: 21, color: color.text }}>{foot}</div>
      </div>
    ),
    { ...SIZE, headers: { "cache-control": cache } },
  );
}

const LABEL = { display: "flex", fontSize: 24, letterSpacing: 2, color: color.muted } as const;

export function shareImage(who: string, game: GameView, card: Card | undefined,
                           cache: string): ImageResponse {
  const world = worldFor(card?.outcome, !!card?.price);
  const p = card?.price;
  const priced = p ? `${american(p.american)} at ${p.book_name}, ${p.minutes_before_kickoff} min `
    + `before kickoff${p.timing === "early" ? " (earlier than usual)" : ""}` : null;
  const d = card?.decided_by;
  const decided = d ? clip(`${d.exact ? "Decided by" : "On or around"}: ${d.text ?? "the game clock"}`
    + ` (${[quarter(d.period), d.clock].filter(Boolean).join(" ")})`, 110) : null;
  return frame(world, game, (
    <>
      <div style={LABEL}>{clip(`${who}${card ? ` · ${card.label}` : ""}`.toUpperCase(), 56)}</div>
      <div style={{ display: "flex", fontSize: 38, fontWeight: 700, marginTop: 6 }}>
        {card ? clip(card.bet, 46) : "No archived prices"}
      </div>
      {card && (
        <div style={{ display: "flex", marginTop: 12 }}>
          <Chip outcome={card.outcome} text={card.outcome_text} />
          {card.verified && (
            <div style={{ display: "flex", alignItems: "center", padding: "6px 18px",
                          borderRadius: 999, marginLeft: 12, background: color.surface3,
                          color: color.muted, fontWeight: 700, fontSize: 26 }}>
              <Icon glyph="☑" ink={color.muted} />Verified
            </div>
          )}
        </div>
      )}
      {card && <Payout outcome={card.outcome} back={card.back} returns={card.returns} />}
      {priced && <div style={{ display: "flex", color: color.muted, marginTop: 2 }}>{priced}</div>}
      {decided && <div style={{ display: "flex", color: color.muted, marginTop: 6,
                                fontSize: 23 }}>{decided}</div>}
    </>
  ), "Hypothetical: what $10 at the price 45 minutes before kickoff would have paid. "
     + "Juju is not a sportsbook.", cache);
}

/** A parlay's image: its legs as lights, the combined payout, and the note every parlay
 *  carries. Only the legs' names and bets are listed, with the parlay's one number. */
export function parlayShareImage(view: ParlayView, cache: string): ImageResponse {
  const world = worldFor(view.outcome, view.returns !== null);
  const shown = view.legs.slice(0, 4);
  const more = view.legs.length - shown.length;
  return frame(world, view.game, (
    <>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={LABEL}>
          {`SAME-GAME PARLAY · ${view.legs.length} LEGS${view.book_name ? ` · ${view.book_name.toUpperCase()}` : ""}`}
        </div>
        <div style={{ display: "flex" }}>
          {view.legs.map((l) => {
            const lit = CASHED.has(l.outcome), out = LOST.has(l.outcome);
            return (
              <div key={l.key} style={{
                display: "flex", width: 22, height: 22, borderRadius: 11, marginLeft: 8,
                background: lit ? color.gold : out ? color.surface3 : color.surface2,
                border: `2px solid ${lit ? color.gold : out ? color.lost : color.lineStrong}`,
              }} />
            );
          })}
        </div>
      </div>
      <div style={{ display: "flex", marginTop: 10 }}>
        <Chip outcome={view.outcome} text={view.outcome_text} />
      </div>
      {view.returns
        ? <Payout outcome={view.outcome} back={view.back} returns={view.returns} />
        : <div style={{ display: "flex", fontSize: 40, fontWeight: 800, marginTop: 14 }}>
            {view.headline}
          </div>}
      <div style={{ display: "flex", flexDirection: "column", marginTop: 4, fontSize: 24 }}>
        {shown.map((l) => (
          <div key={l.key} style={{ display: "flex", color: color.text }}>
            {clip(`${l.who}: ${l.bet}`, 62)}
          </div>
        ))}
        {more > 0 && <div style={{ display: "flex", color: color.muted }}>{`and ${more} more`}</div>}
      </div>
    </>
  ), `Hypothetical. ${view.notes[0] ?? ""} Juju is not a sportsbook.`, cache);
}

/** The card a share link points at (`?card=`), else the page's first card. */
export function pickCard(cards: Card[], key: string | null): Card | undefined {
  return (key && cards.find((c) => c.key === key)) || cards[0];
}

/** The image URL for a page's card (relative; the layout's metadataBase makes it absolute). */
export function imagePath(pagePath: string, card: Card | undefined): string {
  if (!card) return `${pagePath}/image`;
  const q = new URLSearchParams({ card: card.key });
  if (card.alternate && card.line) q.set("threshold", card.line);
  return `${pagePath}/image?${q}`;
}

export const CARD_KEY = /^[a-z0-9_]{1,60}$/;
export const THRESHOLD = /^\d{1,3}(\.5)?$/;
export const LEGS = /^[a-z0-9_.,-]{1,600}$/;
