import { expect, test, type Page } from "@playwright/test";

// The worlds (M3): the price never waits for them, the count-up ends on the API's number,
// the Jujus match the bet's state, and reduced motion shows the final number at once.

async function pastAgeGate(page: Page) {
  await page.addInitScript(() => window.localStorage.setItem("juju-21-confirmed", "yes"));
}

const CASHED = "/g/1/3929630?play=run&yards=17&expect=1";     // Barkley: rush yards locked
const WAITING = "/g/1/4241478?play=catch&yards=45&expect=1";  // Smith: 45-yd catch not in feed

test("the price is in the server's HTML, before any world code runs", async ({ request }) => {
  const html = await (await request.get(CASHED)).text();
  const text = html.replace(/<!-- -->/g, "");
  expect(text).toContain("Over 73.5 rush yards");
  expect(text).toContain("-112 at DraftKings");
  // The server sends the real payout, never the count-up's starting value.
  expect(text).toMatch(/<span class="count" data-final="18.93">\$10 → \$18\.93<\/span>/);
});

test("a cashed card: gold world, jumping Jujus, count-up lands on the exact number", async ({ page }) => {
  await pastAgeGate(page);
  await page.goto(CASHED);
  await expect(page.locator(".world")).toHaveAttribute("data-world", "cash");
  await expect(page.locator("#first-card").locator("..").locator(".crew")).toHaveAttribute("data-mood", "joy");
  const count = page.locator("#first-card .count");
  await expect(count).toHaveText("$10 → $18.93", { timeout: 3000 });
  await expect(count).toHaveAttribute("data-final", "18.93");
  await expect(page.getByText(/Hypothetical/).first()).toBeVisible();
});

test("waiting for the feed: the searchlight world and Bo on binoculars", async ({ page }) => {
  await pastAgeGate(page);
  await page.goto(WAITING);
  await expect(page.locator(".world")).toHaveAttribute("data-world", "wait");
  await expect(page.locator(".crew").first()).toHaveAttribute("data-mood", "look");
});

test("home is calm or lit, never celebrating", async ({ page }) => {
  await pastAgeGate(page);
  await page.goto("/");
  await expect(page.locator(".world")).toHaveAttribute("data-world", /^(live|pre)$/);
  await expect(page.locator(".crew")).not.toHaveAttribute("data-mood", "joy");
});

test.describe("reduced motion", () => {
  test.use({ contextOptions: { reducedMotion: "reduce" } });
  test("shows the final number at once and keeps the Jujus still", async ({ page }) => {
    await pastAgeGate(page);
    await page.goto(CASHED);
    await expect(page.locator("#first-card .count")).toHaveText("$10 → $18.93", { timeout: 500 });
    const moving = await page.locator(".crew .j").first().evaluate(
      (el) => getComputedStyle(el).animationName);
    expect(moving).toBe("none");
  });
});
