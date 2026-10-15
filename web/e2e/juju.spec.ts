import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

// The seeded game is PHI @ CHI (recorded 2026-09-28), in the fourth quarter.

async function pastAgeGate(page: Page) {
  // The server reads the cookie (no gate drawn at all); localStorage is the older way.
  await page.context().addCookies([{ name: "juju21", value: "yes",
                                     url: process.env.BASE_URL ?? "http://127.0.0.1:3000" }]);
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
  await expect(page.getByRole("heading", { name: "On now" })).toBeVisible();
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

test("text naming no one offers the latest plays, and the page names the one tapped",
     async ({ page }) => {
  await pastAgeGate(page);
  await page.goto("/");
  await page.getByRole("combobox").fill("that TD");
  await page.getByRole("button", { name: "Look it up" }).click();
  await expect(page.getByText("Which play? The latest, newest first.")).toBeVisible();
  const plays = page.getByRole("list", { name: "Latest plays" });
  await expect(plays.getByRole("link").first()).toContainText("TD");
  await plays.getByRole("link", { name: /Hurts 1-yd TD run/ }).click();
  await expect(page.getByRole("heading", { name: "Jalen Hurts" })).toBeVisible();
  await expect(page.getByRole("note")).toContainText("J. Hurts 1-yd TD run");
});

test("a game's plays open what they paid, in place", async ({ page }) => {
  await pastAgeGate(page);
  await page.goto("/");
  await page.getByRole("link", { name: /Every play, and what it paid/ }).first().click();
  await expect(page.getByRole("heading", { name: /PHI 7 – 27 CHI/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: "4th quarter" })).toBeVisible();
  const play = page.getByRole("button", { name: /D\. Smith 30-yd catch/ });
  await expect(play).toContainText("from Jalen Hurts");
  await play.click();
  await expect(play).toHaveAttribute("aria-expanded", "true");
  const bet = page.getByRole("article").first();
  await expect(bet).toContainText(/at (DraftKings|FanDuel|BetMGM)/);
  await expect(bet).toContainText("Hypothetical");
  await page.getByRole("link", { name: /All of DeVonta Smith's bets/ }).click();
  await expect(page.getByRole("heading", { name: "DeVonta Smith" })).toBeVisible();
  await expect(page.getByRole("note")).toContainText("D. Smith 30-yd catch");
  await page.getByRole("link", { name: /every play/ }).click();
  await expect(page.getByRole("tab", { name: "Plays" })).toHaveAttribute("aria-selected", "true");
});

test("team lines", async ({ page }) => {
  await pastAgeGate(page);
  await page.goto("/g/1");
  await page.getByRole("tab", { name: "Game lines" }).click();
  await page.getByRole("link", { name: "CHI lines" }).click();
  await expect(page.getByRole("heading", { name: "Chicago Bears" })).toBeVisible();
  await expect(page.getByRole("article", { name: /Spread/ })).toContainText("CHI");
});

test("bets with no price are one line each, after the cards", async ({ page }) => {
  await pastAgeGate(page);
  await page.goto("/g/1/team/3");
  const none = page.getByRole("region", { name: "Bets with no price" });
  await expect(none).toContainText("Moneyline");  // the recording has no moneyline
  await expect(page.getByRole("article", { name: /Moneyline/ })).toHaveCount(0);
  await expect(page.getByRole("article").first()).not.toContainText("None");
});

test.describe("a phone in another time zone than the server", () => {
  test.use({ timezoneId: "America/Los_Angeles" });

  test("renders the same page the server sent: no hydration mismatch", async ({ page }) => {
    const problems: string[] = [];
    page.on("console", (m) => {
      // Development builds say "hydration"; production ones give React's error number.
      if (/hydrat|did not match|server rendered|#41[89]|#42[35]/i.test(m.text())) {
        problems.push(m.text());
      }
    });
    page.on("pageerror", (e) => problems.push(e.message));
    await pastAgeGate(page);
    await page.goto("/g/1/3929630");
    await expect(page.getByRole("heading", { name: "Saquon Barkley" })).toBeVisible();
    await page.getByRole("article", { name: /Rush yards/ }).getByText("Details").click();
    // The server sent Eastern time; the phone adds its own beside it, after hydrating.
    await expect(page.getByRole("article", { name: /Rush yards/ }))
      .toContainText(/\d+:\d\d [AP]M \(\d+:\d\d [AP]M E[DS]T\)/);
    expect(problems).toEqual([]);
  });
});

test("pages have no serious accessibility problems", async ({ page }) => {
  await pastAgeGate(page);
  for (const path of ["/", "/about", "/g/1", "/g/1/3929630", "/g/1/team/3"]) {
    await page.goto(path);
    const results = await new AxeBuilder({ page }).analyze();
    const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(bad.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([]);
  }
});
