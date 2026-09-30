import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

// The seeded game is PHI @ CHI (recorded 2026-09-28), in the fourth quarter.

async function pastAgeGate(page: Page) {
  await page.addInitScript(() => window.localStorage.setItem("juju-21-confirmed", "yes"));
}

test("the age gate asks once and remembers", async ({ page }) => {
  await page.goto("/");
  const gate = page.getByRole("dialog", { name: "Are you 21 or older?" });
  await expect(gate).toBeVisible();
  await page.getByRole("button", { name: "I am 21 or older" }).click();
  await expect(gate).toBeHidden();
  await page.reload();
  await expect(gate).toBeHidden();
});

test("tap a live play and get the price at once", async ({ page }) => {
  await pastAgeGate(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Live now" })).toBeVisible();
  await page.getByRole("link", { name: /J\. Hurts 20-yd run/ }).click();
  await expect(page.getByRole("heading", { name: "Jalen Hurts" })).toBeVisible();
  const card = page.getByRole("article", { name: /Rush yards/ });
  await expect(card).toContainText("DraftKings");  // no Hard Rock in the recording
  await expect(card).toContainText(/min before kickoff/);
  await expect(page.getByText(/Juju is not a sportsbook/).first()).toBeVisible();
});

test("typeahead goes straight to a player", async ({ page }) => {
  await pastAgeGate(page);
  await page.goto("/");
  await page.getByRole("combobox").fill("sa");
  await page.getByRole("option", { name: /Saquon Barkley/ }).click();
  await expect(page.getByRole("heading", { name: "Saquon Barkley" })).toBeVisible();
  const rush = page.getByRole("article", { name: /Rush yards/ });
  await expect(rush).toContainText("Over 73.5 rush yards");
  await expect(rush).toContainText("$10 → $18.93");
  await expect(rush).toContainText("Cashed, if the play stands");
  await expect(rush).toContainText(/Without the book's margin/);
});

test("free text with a clear name resolves, a shared name asks", async ({ page }) => {
  await pastAgeGate(page);
  await page.goto("/");
  await page.getByRole("combobox").fill("Smith 45 yd catch");
  await page.getByRole("button", { name: "Look it up" }).click();
  await expect(page.getByText("Which one did you mean?")).toBeVisible();
  await page.getByRole("link", { name: /DeVonta Smith/ }).click();
  await expect(page.getByRole("heading", { name: "DeVonta Smith" })).toBeVisible();
  // His longest catch in the feed is 30 yards: a 45-yarder hasn't reached it yet.
  await expect(page.getByRole("status").filter({ hasText: "isn't in the official feed yet" }))
    .toBeVisible();
});

test("team lines", async ({ page }) => {
  await pastAgeGate(page);
  await page.goto("/");
  await page.getByRole("link", { name: "CHI lines" }).first().click();
  await expect(page.getByRole("heading", { name: "Chicago Bears" })).toBeVisible();
  await expect(page.getByRole("article", { name: /Spread/ })).toContainText("CHI");
});

test("pages have no serious accessibility problems", async ({ page }) => {
  await pastAgeGate(page);
  for (const path of ["/", "/about"]) {
    await page.goto(path);
    const results = await new AxeBuilder({ page }).analyze();
    const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(bad.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([]);
  }
});
