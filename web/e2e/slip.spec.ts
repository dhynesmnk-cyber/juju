import { expect, test, type Page } from "@playwright/test";

// The slip, quick picks and the round robin, on the seeded PHI @ CHI game (fourth quarter).

async function pastAgeGate(page: Page) {
  await page.context().addCookies([{ name: "juju21", value: "yes",
                                     url: process.env.BASE_URL ?? "http://127.0.0.1:3000" }]);
}

test("from a tapped play to a priced parlay in three taps", async ({ page }) => {
  await pastAgeGate(page);
  await page.goto("/g/1");
  await page.getByRole("button", { name: /D\. Smith 30-yd catch/ }).click();  // the tapped play
  const answer = page.locator(".inline-answer");
  await expect(answer.getByRole("article")).toHaveCount(2);
  // Tap 1 and tap 2: both bets the play touched go in the slip.
  await answer.getByRole("button", { name: "Add to parlay" }).first().click();
  await answer.getByRole("button", { name: "Add to parlay" }).first().click();
  const slip = page.getByRole("complementary", { name: "Parlay" });
  // Priced already, in the bar: the count, the book, the combined payout, the caveat.
  await expect(slip).toContainText("Parlay · 2 legs");
  await expect(slip).toContainText("Priced at DraftKings.");
  await expect(slip.locator(".slip-badge")).toHaveText("2");
  const amount = (await slip.locator(".slip-amount .big").textContent()) ?? "";
  expect(amount).toMatch(/^\$\d+\.\d\d$/);
  await expect(slip).toContainText("Standard parlay maths. Books adjust same-game parlays for "
                                   + "correlation, so a real ticket would have paid less.");
  // Tap 3: its own page, with the very same number.
  await slip.getByRole("link", { name: "See what it would have paid" }).click();
  await expect(page.getByRole("heading", { name: "Same-game parlay" })).toBeVisible();
  await expect(page.locator("#parlay-card .payout-amount")).toHaveText(amount);
});

test("the slip opens to a sheet: each leg at the parlay's book, and legs come out", async ({ page }) => {
  await pastAgeGate(page);
  await page.goto("/g/1");
  await page.evaluate(() => window.localStorage.setItem("juju-parlay-1", JSON.stringify([
    { key: "p-3929630-player_rush_yds", who: "Saquon Barkley", bet: "Over 73.5 rush yards" },
    { key: "p-4241478-player_receptions", who: "DeVonta Smith", bet: "Over 5.5 receptions" },
  ])));
  await page.reload();
  const slip = page.getByRole("complementary", { name: "Parlay" });
  await slip.getByRole("button", { name: /Parlay · 2 legs/ }).click();
  const sheet = page.getByRole("dialog", { name: "Parlay slip" });
  await expect(sheet).toBeVisible();
  await expect(sheet).toContainText("-112 at DraftKings");
  await expect(sheet).toContainText("-120 at DraftKings");
  await expect(sheet).toContainText(/Books adjust same-game parlays for correlation/);
  await expect(sheet).toContainText("Hypothetical. Juju is not a sportsbook.");
  await expect(sheet.getByRole("link", { name: "See what it would have paid" })).toBeVisible();
  await sheet.getByRole("button", { name: /^Remove DeVonta Smith/ }).click();
  await expect(sheet).toContainText("Add one more leg");
  await page.keyboard.press("Escape");
  await expect(sheet).toBeHidden();
  await expect(slip).toContainText("Parlay · 1 leg");
});

test("quick picks list no prices and ask for none until one is opened", async ({ page }) => {
  await pastAgeGate(page);
  const asked: string[] = [];
  page.on("request", (r) => { if (/\/parlay/.test(r.url())) asked.push(r.url()); });
  await page.goto("/g/1");
  const picks = page.getByRole("region", { name: "Quick picks" });
  await expect(picks.getByRole("link")).toHaveCount(4);
  await expect(picks).toContainText("No prices until you open one");
  // Names and bets in words: no odds, no dollar amount, no line.
  const text = (await picks.getByRole("list").innerText())
    .replace(/\b20-yard\b|\b\d-leg parlay\b/gi, "");
  expect(text).not.toMatch(/[$]|\d/);
  await picks.getByRole("link").first().hover();
  await page.waitForTimeout(600);
  expect(asked).toEqual([]);  // nothing prefetched: the list is not a price list
  await picks.getByRole("link", { name: /PHI stack/ }).click();
  await expect(page.getByRole("heading", { name: "Same-game parlay" })).toBeVisible();
  const card = page.getByRole("article", { name: /^Parlay/ });
  await expect(card).toContainText("3 legs · DraftKings");
  await expect(card.locator(".payout-amount")).toHaveText(/^\$\d+\.\d\d$/);
  await expect(card).toContainText(/Books adjust same-game parlays for correlation/);
  // It filled the slip, which is waiting back on the game's page.
  await page.getByRole("link", { name: /every play/ }).click();
  await expect(page.getByRole("complementary", { name: "Parlay" })).toContainText("Parlay · 3 legs");
});

test("a three-leg parlay shows its round robin, from the numbers on the card", async ({ page }) => {
  await pastAgeGate(page);
  await page.goto("/g/1/parlay?legs=p-3929630-player_rush_yds,p-4241478-player_receptions,"
                  + "p-15168-player_pass_yds");
  const robin = page.getByRole("region", { name: "Round robin" });
  await expect(robin).toContainText("By twos: 3 parlays, $30.00 in");
  await expect(robin.getByRole("listitem")).toHaveCount(3);
  // -112 and -120: 10 x (212/112) x (220/120) = 34.70, as the two-leg parlay's own page says.
  await expect(robin.getByRole("listitem").first()).toContainText("$34.70");
  await expect(robin).toContainText(/a real ticket would have paid less\. That goes for every/);
  const two = await page.request.get(
    "/api/parlay/1?legs=p-3929630-player_rush_yds,p-4241478-player_receptions");
  expect((await two.json()).returns).toBe("34.70");
  // Two legs have no round robin.
  await page.goto("/g/1/parlay?legs=p-3929630-player_rush_yds,p-4241478-player_receptions");
  await expect(page.getByRole("heading", { name: "Same-game parlay" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Round robin" })).toHaveCount(0);
});

test("a parlay has its own share image", async ({ page, request }) => {
  await pastAgeGate(page);
  const legs = "p-3929630-player_rush_yds,p-4241478-player_receptions";
  await page.goto(`/g/1/parlay?legs=${legs}`);
  const og = await page.locator('meta[property="og:image"]').getAttribute("content");
  expect(og).toContain("/g/1/parlay/image?legs=");
  const image = await request.get(new URL(og!).pathname + new URL(og!).search);
  expect(image.status()).toBe(200);
  expect(image.headers()["content-type"]).toBe("image/png");
  expect((await image.body()).length).toBeGreaterThan(10_000);
  expect((await request.get("/g/1/parlay/image?legs=../../etc")).status()).toBe(404);
  expect((await request.get("/g/1/parlay/image")).status()).toBe(404);
  expect((await request.get(`/g/1/parlay/image?legs=p-1-player_rush_yds`)).status()).toBe(404);
});
