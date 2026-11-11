// The contrast audit: every pairing the stylesheet may use, worked out, never eyeballed.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  color, contrast, cssVariables, juju, luminance, over, pairings, resolve, scrim, scrimmed, world,
  worldGradient,
} from "./tokens.ts";

const DARK = 0.1;  // relative luminance: every layer and every tint is far below it

test("every pairing meets WCAG AA", () => {
  const short = pairings
    .map((p) => ({ ...p, ratio: contrast(color[p.fg], resolve(p.bg)) }))
    .filter((p) => p.ratio < p.min)
    .map((p) => `${p.fg} on ${p.bg}: ${p.ratio.toFixed(2)} < ${p.min} (${p.use})`);
  assert.deepEqual(short, []);
});

test("orange text goes on dark backgrounds only", () => {
  for (const p of pairings.filter((x) => x.fg === "orange" || x.fg === "orangeBright")) {
    assert.ok(luminance(resolve(p.bg)) < DARK, `${p.fg} on ${p.bg}`);
  }
});

test("an orange or gold fill carries near-black text, never white", () => {
  const fills = pairings.filter((p) => ["orange", "orangeBright", "gold"].includes(p.bg));
  assert.ok(fills.length >= 3);
  for (const p of fills) assert.equal(p.fg, "ink", `${p.fg} on ${p.bg}`);
  // Why: white on the primary fails AA outright, so it must never be reached for.
  assert.ok(contrast(color.text, color.orange) < 3);
  assert.ok(contrast(color.ink, color.orange) >= 4.5);
});

test("a win, a loss and the primary are told apart by more than hue", () => {
  // Status is always words and an icon too; these keep the colours themselves distinct.
  assert.ok(contrast(color.gold, color.lost) >= 1.5);
  assert.ok(contrast(color.gold, color.orange) >= 1.5);
});

test("text straight on the page stays AA over anything a world can draw", () => {
  // The worst case behind a scrim is a pure white pixel (a confetti piece, a floodlight).
  for (const alpha of [scrim.hero, scrim.dense]) {
    assert.ok(contrast(color.text, scrimmed(alpha)) >= 4.5, `text over a ${alpha} scrim`);
  }
  // Muted and orange text need more than the hero's scrim gives, which is why neither is ever
  // put straight on the page (e2e/audit.spec.ts checks the rendered pages for it).
  assert.ok(contrast(color.muted, scrimmed(scrim.hero)) < 4.5);
  assert.ok(scrim.dense > scrim.hero);
});

test("the tints are a colour over a card, nothing brighter", () => {
  for (const [tint, from] of [["orangeTint", "orange"], ["goldTint", "gold"], ["lostTint", "lost"],
                              ["warnTint", "warn"], ["dangerTint", "danger"]] as const) {
    assert.ok(luminance(color[tint]) < DARK, tint);
    assert.equal(color[tint], over(color[from], color.surface1, tint === "orangeTint" ? 0.16 : 0.14),
                 tint);
  }
});

test("the worlds are dark pictures in the palette", () => {
  for (const [name, [top, bottom]] of Object.entries(world)) {
    assert.match(top, /^#[0-9A-F]{6}$/, name);
    assert.match(bottom, /^#[0-9A-F]{6}$/, name);
    assert.ok(worldGradient(name as keyof typeof world).includes(top), name);
    assert.equal(worldGradient(name as keyof typeof world, true),
                 `linear-gradient(${top}, ${bottom})`);
    assert.ok(luminance(top) > luminance(bottom), `${name}: the glow is at the top`);
  }
  // Nothing blue is left of the old night sky: every world is neutral or warm.
  for (const [name, stops] of Object.entries(world)) {
    for (const hex of stops) {
      const n = Number.parseInt(hex.slice(1), 16);
      assert.ok((n & 255) <= ((n >> 16) & 255) + 4, `${name} ${hex} leans blue`);
    }
  }
  assert.equal(juju.ink, color.ink);
});

test("the stylesheet names no colour of its own", () => {
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "");
  assert.deepEqual(css.match(/#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(/g) ?? [], []);
  // Every variable it reads is one the tokens define, or one it declares itself.
  const defined = new Set([...cssVariables().matchAll(/--([a-z0-9-]+):/g)].map((m) => m[1]));
  const local = new Set([...css.matchAll(/--([a-z0-9-]+)\s*:/g)].map((m) => m[1]));
  const FROM_ELSEWHERE = new Set(["font-display"]);  // next/font, in app/layout.tsx
  const unknown = [...new Set([...css.matchAll(/var\(--([a-z0-9-]+)/g)].map((m) => m[1]))]
    .filter((v) => !defined.has(v) && !local.has(v) && !FROM_ELSEWHERE.has(v));
  assert.deepEqual(unknown, []);
});

test("the variables come out as one :root rule", () => {
  const css = cssVariables();
  assert.match(css, /^:root\{color-scheme:dark;/);
  assert.ok(css.includes(`--bg:${color.bg}`) && css.includes(`--orange-bright:${color.orangeBright}`));
  assert.ok(css.includes("--surface-1:") && css.includes("--scrim-hero:rgb(13 14 16 / 62%)"));
});
