import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

// The seeded game is PHI @ CHI (recorded 2026-09-28), in the fourth quarter: Barkley has 82
// rushing yards (line 73.5) and DeVonta Smith 6 catches (line 5.5), so both have cashed.

async function pastAgeGate(page: Page) {
  await page.addInitScript(() => window.localStorage.setItem("juju-21-confirmed", "yes"));
}

async function openPlayer(page: Page, typed: string, name: RegExp, heading: string) {
  await page.goto("/");
  await page.getByRole("combobox").fill(typed);
  await page.getByRole("option", { name }).first().click();
  await expect(page.getByRole("heading", { name: heading })).toBeVisible();
}

test("build a parlay from two cards: two lights lit, and it has cashed", async ({ page }) => {
  test.slow();  // two player pages and the parlay: slower on CI's software rendering
  await pastAgeGate(page);
  await openPlayer(page, "sa", /Saquon Barkley/, "Saquon Barkley");
  const rush = page.getByRole("article", { name: /^Rush yards/ });
  await rush.getByRole("button", { name: "Add to parlay" }).click();
  await expect(rush.getByRole("button", { name: "In parlay" })).toHaveAttribute(
    "aria-pressed", "true");
  const tray = page.getByRole("complementary", { name: "Parlay" });
  await expect(tray).toContainText("Parlay: 1 leg");

  await openPlayer(page, "devonta", /DeVonta Smith/, "DeVonta Smith");
  await page.getByRole("article", { name: /^Receptions/ })
    .getByRole("button", { name: "Add to parlay" }).click();
  await expect(tray).toContainText("Parlay: 2 legs");
  await tray.getByRole("link", { name: "See the parlay" }).click();

  await expect(page.getByRole("heading", { name: "Same-game parlay" })).toBeVisible();
  await expect(page.getByRole("list", { name: "2 of 2 legs cashed" })).toBeVisible();
  await expect(page.locator(".world")).toHaveAttribute("data-world", "cash");  // all lit: gold
  const card = page.getByRole("article", { name: /^Parlay/ });
  await expect(card).toContainText("Cashed, if the play stands");
  await expect(card).toContainText("2 legs · DraftKings");
  // Each leg's price is the one its own card shows, at the parlay's book.
  await expect(card).toContainText("-112 at DraftKings");  // Barkley, over 73.5 rush yards
  await expect(card).toContainText("-120 at DraftKings");  // Smith, over 5.5 receptions
  await expect(card).toContainText(/Books adjust same-game parlays for correlation/);
  await expect(card).toContainText("$10 →");
  // Each leg's own price is on its card, one tap away.
  await card.getByRole("link", { name: "Saquon Barkley" }).click();
  await expect(page.getByRole("article").first()).toHaveAccessibleName(/^Rush yards/);
});

test("two bets in one line open the parlay", async ({ page }) => {
  await pastAgeGate(page);
  await page.goto("/");
  await page.getByRole("combobox").fill("Barkley 100+ rush yds and DeVonta Smith 5+ catches");
  await page.getByRole("button", { name: "Look it up" }).click();
  await expect(page.getByRole("heading", { name: "Same-game parlay" })).toBeVisible();
  const card = page.getByRole("article", { name: /^Parlay/ });
  await expect(card).toContainText("100+ rush yards");  // 82 so far: still live
  await expect(card).toContainText("5+ receptions");
  await expect(page.getByRole("list", { name: "1 of 2 legs cashed" })).toBeVisible();
  await expect(page.locator(".world")).toHaveAttribute("data-world", "live");  // not yet gold
  await expect(card).toContainText("if it hits");
});

test("the parlay page has no serious accessibility problems", async ({ page, request }) => {
  await pastAgeGate(page);
  const game = (await (await request.get("/api/live")).json()).games[0].id;
  await page.goto(`/g/${game}/parlay?legs=p-4241478-player_receptions,p-3929630-player_rush_yds`);
  await expect(page.getByRole("heading", { name: "Same-game parlay" })).toBeVisible();
  const results = await new AxeBuilder({ page }).analyze();
  const serious = results.violations.filter((v) => ["serious", "critical"].includes(v.impact ?? ""));
  expect(serious.map((v) => v.id)).toEqual([]);
});
