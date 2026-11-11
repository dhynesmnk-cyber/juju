// The one place Juju's colours live: the orange sportsbook look the owner chose on 2026-11-11
// (docs/GOALS.md, decision 13). app/layout.tsx turns them into CSS variables; the share images,
// the worlds' shader, the Jujus and the passcode pages read them from here, so nothing else
// names a colour. `pairings` lists every text-on-background use, and lib/tokens.test.ts holds
// each to WCAG AA. No "@/" imports: `npm test` runs the lib tests with Node's own test runner.

export const color = {
  // A near-black charcoal base, then three layers of grey.
  bg: "#0D0E10",
  surface1: "#16181B",   // cards and panels
  surface2: "#1E2125",   // rows and fields inside a card
  surface3: "#282C31",   // a pressed row, a neutral chip
  line: "#343940",       // hairlines between rows: decoration, never a control's only edge
  lineStrong: "#747B85", // the edge of a field or an outlined control: 3:1 on every layer
  text: "#F5F6F7",
  muted: "#A9AFB7",
  ink: "#0D0E10",        // the text on an orange or gold fill: near-black, never white
  // The primary. `orange` fills buttons and badges and draws focus; as text it goes on dark
  // only. `orangeBright` is the same hue a step lighter, for small text and a hovered fill.
  orange: "#FF6A00",
  orangeBright: "#FF8126",
  orangeTint: "#3B2517",
  gold: "#FFC53D",       // a win
  goldTint: "#373020",
  lost: "#9AA1A9",       // a loss: desaturated grey, said in words and with an icon too
  lostTint: "#282B2F",
  warn: "#FFB020",       // a stale feed, a degraded banner
  warnTint: "#372D1C",
  danger: "#FF6B6B",     // a failed refresh
  dangerTint: "#372426",
} as const;

export type ColorName = keyof typeof color;

/** How much of the charcoal sits over a world, so text on the page's own background stays
 *  legible whatever the world draws behind it (lib/tokens.test.ts works both out against a pure
 *  white pixel). `hero` is the top of a result page, where the world shows most; `dense` is
 *  everywhere else, and the whole of a page that is mostly lists (home, a game's plays). */
export const scrim = { hero: 0.62, dense: 0.88 } as const;

/** Each world as two colours: the glow at the top of the screen, where a page leaves the world
 *  showing, and the dark it falls away to. Together they are the still picture shown before the
 *  shader starts, instead of it when WebGL is missing or slow, and under NEXT_PUBLIC_WORLDS=off;
 *  the shader's skies are the same pairs. */
export const world = {
  pre: ["#1C1D1F", "#0A0B0D"],     // night over the stadium
  live: ["#2E1E10", "#0D0E10"],    // floodlit: a warm haze where the lights are
  wait: ["#241C12", "#0E0F11"],    // a searchlight's amber
  cash: ["#9A4B07", "#1D0F05"],    // gold under the lights
  won: ["#C25E0B", "#191513"],     // dawn, orange over charcoal
  lost: ["#202123", "#0C0C0D"],    // grey dusk
  calm: ["#1C1D1F", "#0C0D0F"],
  static: ["#232427", "#121314"],
} as const;

/** Pip, Bo and Tuft, in the palette: orange, gold and ash. */
export const juju = {
  ink: color.ink,
  eye: "#FFFFFF",
  cheek: "#FF7A5C",
  pip: { body: "#FF8A24", feet: "#D96A0E", bulb: color.gold },
  bo: { body: color.gold, feet: "#D9A21E" },
  tuft: { body: "#D5D9DF", top: "#EEF0F3", feet: "#A4AAB3" },
  steel: color.lost,       // the wrench, an umbrella's shaft, a pennant's pole
  shade: color.lineStrong, // the umbrella
  lens: "#FFE9B8",
} as const;

export const shape = { radiusSm: "8px", radiusMd: "12px", radiusLg: "16px" } as const;

// --- Contrast (WCAG 2.2) ----------------------------------------------------------------------

function channels(hex: string): [number, number, number] {
  const n = Number.parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export function luminance(hex: string): number {
  const [r, g, b] = channels(hex).map((c) => {
    const s = c / 255;
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** `top` at `alpha` over `under`, as one colour. */
export function over(top: string, under: string, alpha: number): string {
  const [t, u] = [channels(top), channels(under)];
  return `#${t.map((c, i) => Math.round(c * alpha + u[i] * (1 - alpha))
    .toString(16).padStart(2, "0")).join("")}`.toUpperCase();
}

/** The brightest thing a world can put behind the page, seen through a scrim. */
export function scrimmed(alpha: number, behind = "#FFFFFF"): string {
  return over(color.bg, behind, alpha);
}

export interface Pairing {
  fg: ColorName;
  bg: ColorName | "backdropHero" | "backdropDense";
  min: 3 | 4.5;      // AA: 4.5 for text, 3 for large text, icons and the edges of controls
  use: string;
}

const LAYERS = ["bg", "surface1", "surface2", "surface3"] as const;
const onLayers = (fg: ColorName, min: 3 | 4.5, use: string): Pairing[] =>
  LAYERS.map((bg) => ({ fg, bg, min, use }));

/** Every way a colour is put on another. Add a pair here before using it in CSS. */
export const pairings: Pairing[] = [
  ...onLayers("text", 4.5, "body text"),
  ...onLayers("muted", 4.5, "secondary text and micro-labels"),
  ...onLayers("orangeBright", 4.5, "links and live labels"),
  ...onLayers("orange", 4.5, "the wordmark, large orange type"),
  ...onLayers("gold", 4.5, "a win: its payout and its chip"),
  ...onLayers("lost", 4.5, "a loss: its payout and its chip"),
  ...onLayers("warn", 4.5, "a stale feed"),
  ...onLayers("danger", 4.5, "a failed refresh"),
  { fg: "ink", bg: "orange", min: 4.5, use: "the primary button, the slip's badge" },
  { fg: "ink", bg: "orangeBright", min: 4.5, use: "the primary button, hovered" },
  { fg: "ink", bg: "gold", min: 4.5, use: "a touchdown badge, a lit leg" },
  { fg: "ink", bg: "text", min: 4.5, use: "the chosen tab" },
  { fg: "orangeBright", bg: "orangeTint", min: 4.5, use: "a live chip" },
  { fg: "gold", bg: "goldTint", min: 4.5, use: "a cashed chip" },
  { fg: "lost", bg: "lostTint", min: 4.5, use: "a lost chip" },
  { fg: "warn", bg: "warnTint", min: 4.5, use: "the status banner" },
  { fg: "danger", bg: "dangerTint", min: 4.5, use: "an error note" },
  { fg: "text", bg: "orangeTint", min: 4.5, use: "text in a live panel" },
  { fg: "text", bg: "goldTint", min: 4.5, use: "text in a cashed panel" },
  { fg: "text", bg: "warnTint", min: 4.5, use: "text in the status banner" },
  { fg: "text", bg: "lostTint", min: 4.5, use: "text in a lost panel" },
  ...onLayers("lineStrong", 3, "the edge of a field or an outlined button"),
  ...onLayers("orange", 3, "the focus ring, a chosen row's edge"),
  ...onLayers("gold", 3, "a cashed card's edge"),
  // Text straight on the page's background, with a world behind it, is `text` and nothing
  // else: the scrims are set so that holds over any pixel a world can draw.
  { fg: "text", bg: "backdropHero", min: 4.5, use: "headings over a world" },
  { fg: "text", bg: "backdropDense", min: 4.5, use: "headings over a world, further down" },
];

export function resolve(name: Pairing["bg"] | ColorName): string {
  if (name === "backdropHero") return scrimmed(scrim.hero);
  if (name === "backdropDense") return scrimmed(scrim.dense);
  return color[name];
}

// --- CSS ----------------------------------------------------------------------------------------

const kebab = (s: string) => s.replace(/[A-Z0-9]/g, (c) => `-${c.toLowerCase()}`);

function rgba(hex: string, alpha: number): string {
  const [r, g, b] = channels(hex);
  return `rgb(${r} ${g} ${b} / ${Math.round(alpha * 100)}%)`;
}

/** The custom properties every stylesheet rule reads: `--bg`, `--surface-1`, `--orange`, … */
export function cssVariables(): string {
  const vars: [string, string][] = [
    ...Object.entries(color).map(([k, v]): [string, string] => [kebab(k), v]),
    ["scrim-hero", rgba(color.bg, scrim.hero)],
    ["scrim-dense", rgba(color.bg, scrim.dense)],
    ["veil", rgba("#000000", 0.6)],  // behind the 21+ gate and the open slip
    ["radius-sm", shape.radiusSm], ["radius-md", shape.radiusMd], ["radius-lg", shape.radiusLg],
    ["shadow", `0 10px 30px ${rgba("#000000", 0.45)}`],
    ["glow-gold", `0 0 0 1px ${color.gold}, 0 0 32px ${rgba(color.gold, 0.22)}`],
    ["glow-orange", `0 0 0 1px ${color.orange}`],
  ];
  return `:root{color-scheme:dark;${vars.map(([k, v]) => `--${k}:${v}`).join(";")}}`;
}

/** A world's still picture, as a CSS background: its glow from the top of the screen. The
 *  share images take the plain top-to-bottom one, which next/og draws exactly. */
export function worldGradient(name: keyof typeof world, plain = false): string {
  const [top, bottom] = world[name];
  return plain ? `linear-gradient(${top}, ${bottom})`
    : `radial-gradient(ellipse 130% 70% at 50% 0%, ${top}, ${bottom})`;
}
