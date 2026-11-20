import { color } from "../lib/tokens";

// What lib/tokens.test.ts proves about the palette, checked on the pages as drawn: every piece
// of text has AA contrast with the surface under it; text with no surface under it (so a world
// behind it) is the `text` token and nothing else; and everything tappable is at least 44 px.
// Both functions run inside the page (`page.evaluate`), so each stands alone. Shared by
// audit.spec.ts and live.spec.ts.

type Finding = { what: string; why: string };

export function auditText(textToken: string): Finding[] {
  const parse = (c: string): [number, number, number, number] => {
    const m = c.match(/[\d.]+/g)?.map(Number) ?? [0, 0, 0, 0];
    return [m[0], m[1], m[2], m[3] ?? 1];
  };
  const lum = ([r, g, b]: number[]) => {
    const f = (v: number) => { const s = v / 255; return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const out: Finding[] = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Set<Element>();
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const el = n.parentElement;
    if (!el || seen.has(el) || !n.textContent?.trim()) continue;
    seen.add(el);
    const cs = getComputedStyle(el);
    const box = el.getBoundingClientRect();
    if (cs.visibility === "hidden" || cs.display === "none" || box.width < 2 || box.height < 2
        || el.closest("[aria-hidden='true'], .sr-only, [disabled], dialog:not([open])")
        || !el.checkVisibility()) continue;
    // The surface under it: the nearest ancestors' backgrounds, until one is opaque.
    let under: [number, number, number] | null = null;
    const layers: [number, number, number, number][] = [];
    for (let a: Element | null = el; a && a !== document.documentElement; a = a.parentElement) {
      const s = getComputedStyle(a);
      if (s.backgroundImage !== "none" && !a.classList.contains("ticket-tear")) {
        out.push({ what: el.textContent!.trim().slice(0, 40), why: `a picture behind it (${a.className})` });
      }
      const bg = parse(s.backgroundColor);
      if (bg[3] > 0) layers.push(bg);
      if (bg[3] >= 1) break;
      if (a.classList.contains("page")) { layers.length = 0; break; }  // nothing but the page
    }
    if (layers.length && layers[layers.length - 1][3] >= 1) {
      under = [0, 0, 0];
      for (const [r, g, b, alpha] of layers.reverse()) {
        under = [r * alpha + under[0] * (1 - alpha), g * alpha + under[1] * (1 - alpha),
                 b * alpha + under[2] * (1 - alpha)];
      }
    }
    const fg = parse(cs.color);
    const what = `"${el.textContent!.trim().slice(0, 40)}" <${el.tagName.toLowerCase()} class="${el.className}">`;
    if (!under) {
      if (cs.color !== textToken) out.push({ what, why: `straight on the page in ${cs.color}` });
      continue;
    }
    const ink = [0, 1, 2].map((i) => fg[i] * fg[3] + under![i] * (1 - fg[3]));
    const [hi, lo] = [lum(ink), lum(under)].sort((x, y) => y - x);
    const ratio = (hi + 0.05) / (lo + 0.05);
    const size = Number.parseFloat(cs.fontSize);
    const large = size >= 24 || (size >= 18.66 && Number(cs.fontWeight) >= 700);
    if (ratio < (large ? 3 : 4.5)) out.push({ what, why: `contrast ${ratio.toFixed(2)}` });
  }
  return out;
}

export function auditTargets(): Finding[] {
  const out: Finding[] = [];
  const all = document.querySelectorAll<HTMLElement>(
    "a[href], button, input, summary, [role='tab'], [role='option'] a");
  for (const el of all) {
    const cs = getComputedStyle(el);
    if (!el.checkVisibility() || cs.visibility === "hidden"
        || el.closest("[hidden], dialog:not([open])")) continue;
    // A link inside a sentence is as tall as its line (WCAG 2.5.8's inline exception).
    if (cs.display === "inline" && el.tagName === "A" && !el.closest("footer")) continue;
    const r = el.getBoundingClientRect();
    const wide = cs.display === "inline" ? 44 : r.width;  // footer links: padded tall, word-wide
    if (r.height < 43.5 || wide < 43.5) {
      out.push({ what: `"${(el.textContent ?? "").trim().slice(0, 30)}" <${el.tagName.toLowerCase()} class="${el.className}">`,
                 why: `${Math.round(r.width)} x ${Math.round(r.height)}` });
    }
  }
  return out;
}

export const TEXT = (() => {
  const n = Number.parseInt(color.text.slice(1), 16);
  return `rgb(${n >> 16}, ${(n >> 8) & 255}, ${n & 255})`;
})();

