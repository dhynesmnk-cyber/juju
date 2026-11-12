import { expect, test, type Page } from "@playwright/test";

// Visual snapshots: each page, and each world as its still CSS picture. They run only through
// scripts/visual.sh, which builds the site with NEXT_PUBLIC_WORLDS=off (so no shader moves),
// seeds the recorded game in each state in turn, and runs this file inside Playwright's own
// container image, where the fonts and the browser are the same on every machine. VISUAL
// names the seeded state; anywhere else the file is skipped.
const STATE = process.env.VISUAL ?? "";
const BASE = process.env.VISUAL_BASE_URL ?? "http://127.0.0.1:3001";
const PARLAY = "p-3929630-player_rush_yds,p-4241478-player_receptions,p-15168-player_pass_yds";

test.skip(!STATE, "visual snapshots run through scripts/visual.sh");
test.use({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1, isMobile: true,
           hasTouch: true, contextOptions: { reducedMotion: "reduce" } });

async function open(page: Page, path: string, world: string) {
  await page.context().addCookies([{ name: "juju21", value: "yes", url: BASE }]);
  await page.goto(BASE + path);
  await expect(page.locator(".world")).toHaveAttribute("data-world", world);
  await expect(page.locator(".world")).toHaveAttribute("data-renderer", "css");  // worlds off
  await page.evaluate(() => document.fonts.ready);
}

/** What changes from run to run: how long ago the feed was read, and clock times. */
function shot(page: Page, name: string) {
  return expect(page).toHaveScreenshot(`${name}.png`, {
    fullPage: true,
    mask: [page.locator(".fresh"), page.locator(".fixture time, .page-note time, .match-clock time"),
           page.locator(".banner"),
           ...(STATE === "pregame" ? [page.locator(".scorebug")] : [])],  // "Kickoff in 29 min"
    maxDiffPixelRatio: 0.01,
  });
}

const PAGES: Record<string, [string, string, string, ((page: Page) => Promise<void>)?][]> = {
  live: [
    ["home-live", "/", "live"],
    ["game-live", "/g/1", "live"],
    ["game-play-open", "/g/1", "live", async (page) => {
      await page.getByRole("button", { name: /D\. Smith 30-yd catch/ }).click();
      await expect(page.locator(".inline-answer").getByRole("article")).toHaveCount(2);
    }],
    ["game-slip", "/g/1", "live", async (page) => {
      await page.getByRole("button", { name: /D\. Smith 30-yd catch/ }).click();
      const add = page.locator(".inline-answer").getByRole("button", { name: "Add to parlay" });
      await add.first().click();
      await add.first().click();
      await expect(page.locator(".slip-amount .big")).toBeVisible();
    }],
    ["card-cash", "/g/1/3929630?play=run&yards=17&expect=1", "cash"],
    ["card-live", "/g/1/4040715", "live"],
    ["card-wait", "/g/1/4241478?play=catch&yards=45&expect=1", "wait"],
    ["card-calm-no-prices", "/g/1/17427", "calm"],
    ["team-lines", "/g/1/team/3", "live"],
    ["parlay-cash", `/g/1/parlay?legs=${PARLAY}`, "cash"],
  ],
  final: [
    ["home-pre", "/", "pre"],
    ["game-calm", "/g/1", "calm"],
    ["card-won", "/g/1/3929630", "won"],
    ["card-lost", "/g/1/4040715", "lost"],
    ["parlay-won", `/g/1/parlay?legs=${PARLAY}`, "won"],
  ],
  stale: [["card-static", "/g/1/3929630", "static"]],
  pregame: [
    ["home-next", "/", "pre"],
    ["card-pre", "/g/1/3929630", "pre"],
  ],
};

for (const [name, path, world, act] of PAGES[STATE] ?? []) {
  test(`${name} (${world})`, async ({ page }) => {
    await open(page, path, world);
    if (act) await act(page);
    await page.evaluate(() => window.scrollTo(0, 0));  // masks are placed from the top
    await page.waitForTimeout(300);  // the answer and the slip have drawn
    await shot(page, name);
  });
}

if (STATE === "live") {
  test("how it works", async ({ page }) => {
    await page.context().addCookies([{ name: "juju21", value: "yes", url: BASE }]);
    await page.goto(`${BASE}/about`);
    await shot(page, "about");
  });
  // The share images are drawn on the server, so they are the same bytes' worth of picture
  // wherever the test runs.
  for (const [name, path] of [["share-card", "/g/1/3929630/image?card=player_rush_yds"],
                              ["share-parlay", `/g/1/parlay/image?legs=${PARLAY}`]]) {
    test(name, async ({ request }) => {
      const image = await request.get(BASE + path);
      expect(image.status()).toBe(200);
      expect(await image.body()).toMatchSnapshot(`${name}.png`, { maxDiffPixelRatio: 0.01 });
    });
  }
}
