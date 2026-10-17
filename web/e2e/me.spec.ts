import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

// The owners' own section (/me) on the seeded PHI @ CHI game, in its fourth quarter. Runs only
// when the site and the API were started with TRACKER_USERS, and Playwright with the same (CI
// sets test-only passcodes; web/README.md has the local recipe). The database keeps lookups
// between local runs, so these check what is there, never how many.
const OWNERS = process.env.TRACKER_USERS ?? "";
const [NAME, CODE] = (OWNERS.split(",")[0] ?? "").split(":");

test.skip(!OWNERS, "needs TRACKER_USERS (web/README.md)");

async function pastAgeGate(page: Page) {
  await page.context().addCookies([{ name: "juju21", value: "yes",
                                     url: process.env.BASE_URL ?? "http://127.0.0.1:3000" }]);
}

async function signIn(page: Page) {
  await page.goto("/me");
  await page.getByLabel("Name").fill(NAME);
  await page.getByLabel("Passcode").fill(CODE);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Your lookups" })).toBeVisible();
}

function saved(page: Page) {
  return page.waitForResponse((r) => r.url().endsWith("/api/me/lookups")
                                     && r.request().method() === "POST");
}

test("signed out, the owners' section asks who you are and its API refuses", async ({ page }) => {
  await pastAgeGate(page);
  const first = await page.goto("/me");
  expect(first?.status()).toBe(401);
  await page.getByLabel("Name").fill(NAME);
  await page.getByLabel("Passcode").fill("not-the-passcode");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toContainText("didn't match");
  const api = await page.request.get("/api/me/recaps");
  expect(api.status()).toBe(401);
  expect(await api.json()).toMatchObject({ locked: true });
});

test("signed out, a bet page saves nothing and shows nothing private", async ({ page }) => {
  await pastAgeGate(page);
  const calls: string[] = [];
  page.on("request", (r) => { if (r.url().includes("/api/me")) calls.push(r.url()); });
  await page.goto("/g/1/3929630");
  await expect(page.getByRole("heading", { name: "Saquon Barkley" })).toBeVisible();
  await page.waitForTimeout(500);
  expect(calls).toEqual([]);
  await expect(page.getByRole("link", { name: "Your lookups" })).toHaveCount(0);
  await expect(page.getByText(/Your earlier lookups/)).toHaveCount(0);
});

test("a bet page opened shows in that game's recap, and history says what came before",
     async ({ page }) => {
  await pastAgeGate(page);
  await signIn(page);
  const post = saved(page);
  await page.goto("/g/1/3929630?market=player_rush_yds");
  expect((await post).status()).toBe(200);
  // The seed has one game, so nothing came before it.
  await expect(page.getByRole("heading", { name: "Your earlier lookups of Saquon Barkley" }))
    .toBeVisible();
  await expect(page.getByText("No earlier lookups of Saquon Barkley.")).toBeVisible();
  await page.goto("/me");
  await page.getByRole("link", { name: /PHI .* CHI/ }).first().click();
  const item = page.getByRole("listitem").filter({ hasText: "Saquon Barkley · Rush yards" });
  await expect(item.getByRole("article", { name: /Rush yards/ })).toContainText(
    "Over 73.5 rush yards");
  await expect(item).toContainText("DraftKings");
});

test("a play opened on a game's page is saved; Remove takes it out", async ({ page }) => {
  await pastAgeGate(page);
  await signIn(page);
  await page.goto("/g/1");
  const post = saved(page);
  await page.getByRole("button", { name: /J\. Hurts 20-yd run/ }).click();
  expect((await post).status()).toBe(200);
  await page.goto("/me/recaps/1");
  const item = page.getByRole("listitem").filter({ hasText: "J. Hurts 20-yd run" });
  await expect(item).toBeVisible();
  const gone = page.waitForResponse((r) => r.request().method() === "DELETE");
  await item.getByRole("button", { name: /^Remove Jalen Hurts/ }).click();
  expect((await gone).status()).toBe(200);
  await expect(page.getByRole("status").filter({ hasText: "Removed Jalen Hurts" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("listitem").filter({ hasText: "J. Hurts 20-yd run" }))
    .toHaveCount(0);
});

test("the owners' pages have no serious accessibility problems", async ({ page }) => {
  await pastAgeGate(page);
  await signIn(page);
  const post = saved(page);
  await page.goto("/g/1/3929630");
  await post;
  for (const path of ["/me", "/me/recaps/1"]) {
    await page.goto(path);
    const results = await new AxeBuilder({ page }).analyze();
    const serious = results.violations.filter((v) => ["serious", "critical"].includes(v.impact ?? ""));
    expect(serious, `${path}: ${serious.map((v) => v.id).join(", ")}`).toEqual([]);
  }
});
