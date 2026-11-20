import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { auditTargets, auditText, TEXT } from "./auditLib";

// The live chat (docs/plans/live-chat.md) on the seeded PHI @ CHI game, answered from the
// recordings (`LIVE_ODDS_FIXTURE=1`: no key, no credits). The live chat exists only behind a
// passcode, so this runs only against a site started with SITE_PASSCODE, with BASE_URL pointing
// at it and LIVE_E2E=1 (web/README.md; CI starts a second site for it).
const CODE = process.env.LIVE_E2E_PASSCODE ?? "kite-ember-9417";
const BASE = process.env.BASE_URL ?? "http://127.0.0.1:3000";
const GAME = "Philadelphia Eagles @ Chicago Bears";

test.skip(!process.env.LIVE_E2E, "needs LIVE_E2E=1 and a site with a passcode (web/README.md)");

async function unlock(page: Page) {
  await page.context().addCookies([{ name: "juju21", value: "yes", url: BASE }]);
  await page.goto("/");
  await page.getByLabel("Passcode").fill(CODE);
  await page.getByRole("button", { name: "Open" }).click();
  await expect(page.getByRole("heading", { name: "Live odds, on demand" })).toBeVisible();
}

async function openGame(page: Page) {
  await unlock(page);
  await page.getByRole("link", { name: new RegExp(GAME) }).click();
  await expect(page.getByRole("heading", { name: GAME })).toBeVisible();
}

const box = (page: Page) => page.getByLabel("Ask about a player or team");

/** Ask in the box and wait for the API's answer, whose JSON is returned. */
async function ask(page: Page, text: string) {
  await box(page).fill(text);
  const response = page.waitForResponse((r) => r.url().endsWith("/api/live/ask"));
  await box(page).press("Enter");
  const body = await (await response).json();
  await expect(page.locator(".reply").last()).not.toContainText("Checking live prices");
  return body;
}

test("the live chat is locked behind the passcode", async ({ page, request }) => {
  const api = await request.get("/api/live/games");
  expect(api.status()).toBe(401);
  expect((await api.json()).locked).toBe(true);
  const ask = await request.post("/api/live/ask", { data: { game_id: "abcdef", text: "x y z" } });
  expect(ask.status()).toBe(401);
  const first = await page.goto("/live/47dc7baa254659f3beb2ed2b38c207b6");
  expect(first?.status()).toBe(401);
  await expect(page.getByRole("heading", { name: "Private preview" })).toBeVisible();
});

test("the front page is the live schedule, with the archive one tap away", async ({ page }) => {
  await unlock(page);
  await expect(page.getByRole("link", { name: new RegExp(GAME) })).toBeVisible();
  await expect(page.getByRole("link", { name: /Pittsburgh Steelers @ Cleveland Browns/ }))
    .toBeVisible();
  await expect(page.locator(".credits")).toContainText("odds credits left");
  await expect(page.getByText("Opening a game costs 1 credit")).toBeVisible();
  // The schedule shows no prices.
  expect(await page.locator("main").innerText()).not.toMatch(/[+-]\d{3}\b/);
  await page.getByRole("navigation", { name: "Site" }).getByRole("link", { name: "Archive" }).click();
  await expect(page.getByRole("heading", { name: "What would that play have paid?" })).toBeVisible();
  // The archive's own pages still work, and lead back to the archive, not the live front page.
  await page.goto("/g/1/3929630");
  await page.getByRole("link", { name: /Look up another play/ }).click();
  await expect(page).toHaveURL(/\/archive$/);
});

test("pick a game, see what's posted, ask, and get a plain answer with every book", async ({ page }) => {
  await openGame(page);
  await expect(page.locator(".markets")).toContainText("Rush yards");
  await expect(page.locator(".markets")).toContainText("Player props");
  await expect(box(page)).toBeFocused();                // ready to type, no tap needed

  await box(page).fill("Barkley over 73.5 rushing yards");
  await expect(page.locator(".cost")).toContainText(/This will cost|Free/);  // before asking
  const body = await ask(page, "Barkley over 73.5 rushing yards");
  expect(body.kind).toBe("answer");

  const reply = page.locator(".reply").last();
  await expect(reply).toContainText("Saquon Barkley Over 73.5 rush yards pays -112 at DraftKings right now");
  await expect(reply).toContainText("$10 would return $18.93 if it hits.");
  const card = reply.getByRole("article", { name: /Over 73.5 rush yards/ });
  await expect(card).toContainText("-112");
  await expect(card).toContainText("$10 → $18.93");
  await expect(card).toHaveCount(1);                    // only DraftKings posts 73.5
  await expect(card).toContainText("Under 73.5: -112");  // the other side, same line, same book
  await expect(reply).toContainText("Saquon Barkley has 82 rush yards so far.");
  await expect(reply.locator(".as-of")).toContainText("Live price as of");

  // The credits on screen are The Odds API's own count, as the answer carried it.
  await expect(page.locator(".credits")).toContainText(
    `${body.credits.remaining.toLocaleString("en-US")} odds credits left`);
  await expect(box(page)).toBeFocused();                // and ready for the next one
  await expect(box(page)).toHaveValue("");
});

test("a repeat inside the minute uses no credits", async ({ page }) => {
  await openGame(page);
  const first = await ask(page, "DeVonta Smith got 6 catches");     // Over 5.5 receptions
  expect(first.message).toContain("DeVonta Smith Over 5.5 receptions pays -120 at DraftKings");
  const card = page.locator(".reply").last().getByRole("article", { name: /Over 5.5 receptions/ });
  await expect(card.getByRole("list", { name: "Other books" }))
    .toContainText("FanDuel-114BetMGM+100BetRivers-121");   // each book's own price, chain order
  const again = await ask(page, "DeVonta Smith got 6 catches");
  expect(again.cost).toBe(0);
  expect(again.credits.remaining).toBe(first.credits.remaining);
  await expect(page.locator(".reply").last().locator(".as-of"))
    .toContainText("from the last minute's prices · no credits used");
});

test("a bet the books don't post says so plainly", async ({ page }) => {
  await openGame(page);
  const body = await ask(page, "Klein got 4 catches");
  expect(body.kind).toBe("unavailable");
  const reply = page.locator(".reply").last();
  await expect(reply).toContainText("Market not available");
  await expect(reply).toHaveClass(/problem/);
  await expect(reply.getByRole("article")).toHaveCount(0);   // no price, so no price card
  await expect(reply.locator(".as-of")).toContainText("Checked at");  // not "Live price as of"
  expect(await reply.innerText()).not.toMatch(/\$10 would return/);
});

test("a parlay of live legs, priced at one book, with the correlation note", async ({ page }) => {
  await openGame(page);
  const body = await ask(page, "Barkley over 73.5 rushing yards and DeVonta Smith got 6 catches");
  expect(body.kind).toBe("parlay");
  const reply = page.locator(".reply").last();
  await expect(reply).toContainText("Your 2-leg parlay pays about $34.70 on $10 at DraftKings");
  await expect(reply).toContainText("Books adjust same-game parlays for correlation");
  await expect(reply.getByRole("article")).toHaveCount(2);
});

test("the chat is kept on this device, and a minute-old price is marked as old", async ({ page }) => {
  await page.clock.install();
  await openGame(page);
  await ask(page, "Barkley over 73.5 rushing yards");
  await page.reload();
  await expect(page.locator(".turn")).toHaveCount(1);
  await expect(page.locator(".you").first()).toHaveText("Barkley over 73.5 rushing yards");
  await expect(page.locator(".reply").first()).toContainText("Saquon Barkley Over 73.5");
  await expect(page.locator(".stale-note")).toHaveCount(0);
  await page.clock.fastForward(70_000);                 // a little over a minute
  await expect(page.locator(".stale-note")).toContainText("over a minute old and may have moved");
  await expect(page.locator(".reply.stale")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Ask again" })).toBeVisible();
});

test("the pages pass the contrast, over-the-world and 44 px audit, and axe", async ({ page }) => {
  await unlock(page);
  for (const path of ["/", "/archive"]) {
    await page.goto(path);
    await page.waitForTimeout(400);
    expect(await page.evaluate(auditText, TEXT), `${path} text`).toEqual([]);
    expect(await page.evaluate(auditTargets), `${path} targets`).toEqual([]);
  }
  await page.goto("/");
  await page.getByRole("link", { name: new RegExp(GAME) }).click();
  await expect(page.getByRole("heading", { name: GAME })).toBeVisible();
  await ask(page, "Barkley over 73.5 rushing yards");
  await ask(page, "Klein got 4 catches");
  await ask(page, "Barkley over 73.5 rushing yards and DeVonta Smith got 6 catches");
  await page.waitForTimeout(400);
  expect(await page.evaluate(auditText, TEXT), "chat text").toEqual([]);
  expect(await page.evaluate(auditTargets), "chat targets").toEqual([]);
  const results = await new AxeBuilder({ page }).analyze();
  const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([]);
});
