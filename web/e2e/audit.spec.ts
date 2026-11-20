import { expect, test, type Page } from "@playwright/test";
import { auditTargets, auditText, TEXT } from "./auditLib";

// What lib/tokens.test.ts proves about the palette, checked on the pages as drawn: every piece
// of text has AA contrast with the surface under it; text with no surface under it (so a world
// behind it) is the `text` token and nothing else; and everything tappable is at least 44 px.

async function pastAgeGate(page: Page) {
  await page.context().addCookies([{ name: "juju21", value: "yes",
                                     url: process.env.BASE_URL ?? "http://127.0.0.1:3000" }]);
}

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
