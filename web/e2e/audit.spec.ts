import { expect, test, type Page } from "@playwright/test";
import { color } from "../lib/tokens";

// What lib/tokens.test.ts proves about the palette, checked on the pages as drawn: every piece
// of text has AA contrast with the surface under it; text with no surface under it (so a world
// behind it) is the `text` token and nothing else; and everything tappable is at least 44 px.

async function pastAgeGate(page: Page) {
  await page.context().addCookies([{ name: "juju21", value: "yes",
                                     url: process.env.BASE_URL ?? "http://127.0.0.1:3000" }]);
}

type Finding = { what: string; why: string };

function auditText(textToken: string): Finding[] {
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

function auditTargets(): Finding[] {
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

const TEXT = (() => {
  const n = Number.parseInt(color.text.slice(1), 16);
  return `rgb(${n >> 16}, ${(n >> 8) & 255}, ${n & 255})`;
})();

const PARLAY = "/g/1/parlay?legs=p-3929630-player_rush_yds,p-4241478-player_receptions,p-15168-player_pass_yds";
const PAGES: [string, string, ((page: Page) => Promise<void>)?][] = [
  ["home", "/"],
  ["a game", "/g/1"],
  ["a game, a play open and two legs in the slip", "/g/1", async (page) => {
    await page.getByRole("button", { name: /D\. Smith 30-yd catch/ }).click();
    const add = page.locator(".inline-answer").getByRole("button", { name: "Add to parlay" });
    await add.first().click();
    await add.first().click();
    await expect(page.locator(".slip-amount .big")).toBeVisible();
  }],
  ["the slip's sheet", "/g/1", async (page) => {
    await page.evaluate(() => window.localStorage.setItem("juju-parlay-1", JSON.stringify([
      { key: "p-3929630-player_rush_yds", who: "Saquon Barkley", bet: "Over 73.5 rush yards" },
      { key: "p-4241478-player_receptions", who: "DeVonta Smith", bet: "Over 5.5 receptions" },
    ])));
    await page.reload();
    await page.getByRole("button", { name: /Parlay · 2 legs/ }).click();
    await expect(page.getByRole("dialog", { name: "Parlay slip" })).toContainText("-112 at");
  }],
  ["a game's players", "/g/1", async (page) => { await page.getByRole("tab", { name: "Players" }).click(); }],
  ["a cashed card", "/g/1/3929630?play=run&yards=17&expect=1", async (page) => {
    await page.locator("details.more summary").first().click();
  }],
  ["a waiting card", "/g/1/4241478?play=catch&yards=45&expect=1"],
  ["a live card", "/g/1/4040715"],
  ["team lines", "/g/1/team/3"],
  ["a parlay and its round robin", PARLAY],
  ["how it works", "/about"],
];

for (const [name, path, act] of PAGES) {
  test(`${name}: text contrast, text over the world, and 44 px targets`, async ({ page }) => {
    await pastAgeGate(page);
    await page.goto(path);
    if (act) await act(page);
    await page.waitForTimeout(400);  // fonts and the slip have settled
    expect(await page.evaluate(auditText, TEXT), "text").toEqual([]);
    expect(await page.evaluate(auditTargets), "tap targets").toEqual([]);
  });
}

test("the worlds keep their guardrails: Save-Data stays on the still picture", async ({ browser }) => {
  const context = await browser.newContext();
  await context.addInitScript(() => {
    Object.defineProperty(navigator, "connection", { configurable: true, value: { saveData: true } });
  });
  const page = await context.newPage();
  await pastAgeGate(page);
  await page.goto("/g/1/3929630?play=run&yards=17&expect=1");
  await page.waitForTimeout(2200);  // past the idle start
  await expect(page.locator(".world")).toHaveAttribute("data-renderer", "css");
  await expect(page.locator(".world")).toHaveAttribute("data-world", "cash");
  await context.close();
});

test("the shader waits for the browser to be idle, and the hypothetical line shows through it", async ({ page }) => {
  await pastAgeGate(page);
  await page.goto("/g/1/3929630?play=run&yards=17&expect=1", { waitUntil: "commit" });
  // First paint: the price is there and the world is still the CSS picture.
  await expect(page.locator("#first-card .payout-amount")).toHaveText("$18.93");
  expect(await page.locator(".world").getAttribute("data-renderer")).toBe("css");
  await expect(page.locator("#first-card").getByText("Hypothetical. Juju is not a sportsbook."))
    .toBeInViewport();
});

test("the game page and the home page never celebrate", async ({ page }) => {
  await pastAgeGate(page);
  await page.goto("/g/1");
  await page.getByRole("button", { name: /D\. Smith 30-yd catch/ }).click();
  await expect(page.locator(".inline-answer .crew")).toHaveAttribute("data-mood", "joy");
  await expect(page.locator(".world")).toHaveAttribute("data-world", "live");
  await expect(page.locator(".world")).toHaveAttribute("data-scrim", "dense");
  await page.goto("/");
  await expect(page.locator(".world")).toHaveAttribute("data-scrim", "dense");
});
