// The share image (Open Graph, 1200x630): one card, in its world, with the Jujus. Built with
// next/og from the same view the page shows, so every number on it is the card's own. No font
// is fetched: next/og's built-in font keeps the image working without the network.
import { ImageResponse } from "next/og";
import { stillJujuSvg } from "@/components/world/Jujus";
import { FALLBACK, MOOD, worldFor } from "@/components/world/worlds";
import { american, quarter } from "@/lib/format";
import type { Card, GameView, Outcome } from "@/lib/types";

export const SIZE = { width: 1200, height: 630 };
const SETTLED = new Set<Outcome>(["won", "lost", "push", "void", "no_stat_line"]);

const CHIP: Record<Outcome, [string, string, string]> = {  // icon, text colour, background
  won: ["✓", "#7ee2b0", "rgba(24,120,80,.35)"], locked: ["✓", "#7ee2b0", "rgba(24,120,80,.35)"],
  lost: ["✕", "#ff9a9a", "rgba(150,40,40,.35)"], gone: ["✕", "#ff9a9a", "rgba(150,40,40,.35)"],
  live: ["●", "#8fd3ff", "rgba(30,90,150,.35)"], waiting_for_feed: ["…", "#8fd3ff",
    "rgba(30,90,150,.35)"],
  pregame: ["◷", "#c9cfdb", "rgba(120,130,150,.3)"], push: ["=", "#c9cfdb", "rgba(120,130,150,.3)"],
  void: ["=", "#c9cfdb", "rgba(120,130,150,.3)"], no_stat_line: ["–", "#c9cfdb",
    "rgba(120,130,150,.3)"],
  untracked: ["–", "#c9cfdb", "rgba(120,130,150,.3)"], unavailable: ["!", "#c9cfdb",
    "rgba(120,130,150,.3)"],
};

// The image font has no ✓ ✕ ◷ ☑, so those icons are drawn; the rest are plain characters.
function Icon({ glyph, color }: { glyph: string; color: string }) {
  const line = { stroke: color, strokeWidth: 3, fill: "none", strokeLinecap: "round" as const };
  const path = glyph === "✓" || glyph === "☑" ? <path d="M5 12.5l4.5 4.5L19 7.5" {...line} />
    : glyph === "✕" ? <path d="M6 6l12 12M18 6L6 18" {...line} />
    : glyph === "◷" ? <path d="M12 3a9 9 0 1 0 0.01 0M12 7v5l3 2" {...line} />
    : null;
  if (!path) return <div style={{ display: "flex", marginRight: 10 }}>{glyph}</div>;
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" style={{ marginRight: 10 }}>{path}</svg>
  );
}

function svgSrc(svg: string): string {
  return `data:image/svg+xml;base64,${Buffer.from(svg).toString("base64")}`;
}

function clip(text: string, n: number): string {
  return text.length > n ? `${text.slice(0, n - 1)}…` : text;
}

/** How long the image may be cached: a settled card no longer changes. */
export function imageCache(game: GameView, card: Card | undefined): string {
  const done = game.status === "final" && (!card || SETTLED.has(card.outcome));
  return done ? "public, max-age=3600, s-maxage=86400"
    : "public, max-age=30, s-maxage=30, stale-while-revalidate=60";
}

export function shareImage(who: string, game: GameView, card: Card | undefined,
                           cache: string): ImageResponse {
  const world = worldFor(card?.outcome, !!card?.price);
  const mood = MOOD[world];
  const score = game.home.score !== null && game.away.score !== null
    ? `${game.away.abbr ?? game.away.name} ${game.away.score} – ${game.home.score} ${game.home.abbr ?? game.home.name}`
    : game.label;
  const [icon, ink, chipBg] = card ? CHIP[card.outcome] : CHIP.pregame;
  const p = card?.price;
  const priced = p ? `${american(p.american)} at ${p.book_name}, ${p.minutes_before_kickoff} min `
    + `before kickoff${p.timing === "early" ? " (earlier than T-45)" : ""}` : null;
  // "$10 → $18.93 if it hits": the amount large, the condition beside it.
  const headline = card ? card.headline : "—";
  const cut = headline.indexOf(" if ");
  const [amount, qualifier] = cut > 0 ? [headline.slice(0, cut), headline.slice(cut + 1)]
    : [headline, null];
  const d = card?.decided_by;
  const decided = d ? clip(`${d.exact ? "Decided by" : "On or around"}: ${d.text ?? "the game clock"}`
    + ` (${[quarter(d.period), d.clock].filter(Boolean).join(" ")})`, 110) : null;
  return new ImageResponse(
    (
      <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column",
                    backgroundImage: FALLBACK[world], color: "#f4f6fb", padding: "40px 56px",
                    fontSize: 28 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center",
                      color: "#c9cfdb" }}>
          <div style={{ display: "flex", fontSize: 44, fontWeight: 800, color: "#fff" }}>
            ju<span style={{ color: "#ffc53d" }}>ju</span>
          </div>
          <div style={{ display: "flex" }}>{`${score} · ${game.status_text}`}</div>
        </div>
        <div style={{ display: "flex", flex: 1, alignItems: "center", margin: "16px 0" }}>
          <div style={{ display: "flex", flexDirection: "column", width: 820, padding: "26px 36px",
                        borderRadius: 28, background: "rgba(8,10,18,.72)",
                        border: "2px solid rgba(255,255,255,.12)" }}>
            <div style={{ display: "flex", fontSize: 26, color: "#aab2c3" }}>
              {clip(`${who}${card ? ` · ${card.label}` : ""}`.toUpperCase(), 60)}
            </div>
            <div style={{ display: "flex", fontSize: 38, fontWeight: 700, marginTop: 6 }}>
              {card ? clip(card.bet, 48) : "No archived prices"}
            </div>
            {card && (
              <div style={{ display: "flex", marginTop: 14 }}>
                <div style={{ display: "flex", alignItems: "center", padding: "6px 18px",
                              borderRadius: 999, background: chipBg, color: ink,
                              fontWeight: 700 }}>
                  <Icon glyph={icon} color={ink} />{card.outcome_text}
                </div>
                {card.verified && (
                  <div style={{ display: "flex", alignItems: "center", padding: "6px 18px",
                                borderRadius: 999, marginLeft: 12,
                                background: "rgba(120,130,150,.3)", color: "#c9cfdb",
                                fontWeight: 700 }}>
                    <Icon glyph="☑" color="#c9cfdb" />Verified
                  </div>
                )}
              </div>
            )}
            <div style={{ display: "flex", alignItems: "baseline", marginTop: 10 }}>
              <div style={{ display: "flex", fontSize: 80, fontWeight: 800, letterSpacing: -1 }}>
                {amount}
              </div>
              {qualifier && <div style={{ display: "flex", fontSize: 34, marginLeft: 16,
                                          color: "#c9cfdb" }}>{qualifier}</div>}
            </div>
            {priced && <div style={{ display: "flex", color: "#c9cfdb", marginTop: 6 }}>{priced}</div>}
            {decided && <div style={{ display: "flex", color: "#c9cfdb", marginTop: 8,
                                      fontSize: 24 }}>{decided}</div>}
          </div>
          <div style={{ display: "flex", alignItems: "flex-end", marginLeft: 28, height: 220 }}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={svgSrc(stillJujuSvg("pip", mood))} width={96} height={112} alt="" />
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={svgSrc(stillJujuSvg("bo", mood))} width={70} height={112} alt=""
                 style={{ marginLeft: 6 }} />
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={svgSrc(stillJujuSvg("tuft", mood))} width={96} height={112} alt=""
                 style={{ marginLeft: 6 }} />
          </div>
        </div>
        <div style={{ display: "flex", fontSize: 22, color: "#aab2c3" }}>
          Hypothetical: what $10 at the price 45 minutes before kickoff would have paid. Juju is
          not a sportsbook.
        </div>
      </div>
    ),
    { ...SIZE, headers: { "cache-control": cache } },
  );
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
