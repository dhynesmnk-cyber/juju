import { expect, test } from "@playwright/test";

// The seeded game is PHI @ CHI (recorded 2026-09-28), in the fourth quarter.

type Shared = { __shared?: ShareData };

test("share a card: its own link, first on the page, with its own preview", async ({
  page, request,
}) => {
  await page.addInitScript(() => {
    window.localStorage.setItem("juju-21-confirmed", "yes");
    Object.defineProperty(navigator, "share", {
      configurable: true,
      value: async (data: ShareData) => { (window as unknown as Shared).__shared = data; },
    });
  });
  await page.goto("/");
  await page.getByRole("combobox").fill("sa");
  await page.getByRole("option", { name: /Saquon Barkley/ }).click();
  await expect(page.getByRole("heading", { name: "Saquon Barkley" })).toBeVisible();

  const receptions = page.getByRole("article", { name: /^Receptions/ });
  await receptions.getByRole("button", { name: "Share" }).click();
  const shared = await page.evaluate(() => (window as unknown as Shared).__shared);
  expect(shared?.url).toMatch(/\/g\/\d+\/3929630\?card=player_receptions$/);
  expect(shared?.text).toMatch(/^Over 2\.5 receptions: \$10 → \$/);

  // The link opens with that card first, and its preview is that card.
  await page.goto(shared!.url!);
  await expect(page.getByRole("article").first()).toHaveAccessibleName(/^Receptions/);
  const og = await page.locator('meta[property="og:image"]').getAttribute("content");
  expect(og).toContain("/image?card=player_receptions");
  const image = await request.get(new URL(og!).pathname + new URL(og!).search);
  expect(image.status()).toBe(200);
  expect(image.headers()["content-type"]).toBe("image/png");
  expect((await image.body()).length).toBeGreaterThan(10_000);
});

test("share images refuse malformed input and fall back to the first card", async ({
  request,
}) => {
  const live = await (await request.get("/api/live")).json();
  const game = live.games[0].id;
  expect((await request.get(`/g/${game}/3929630/image?card=../../etc`)).status()).toBe(404);
  expect((await request.get(`/g/${game}/3929630/image?threshold=abc`)).status()).toBe(404);
  const fallback = await request.get(`/g/${game}/3929630/image?card=no_such_market`);
  expect(fallback.status()).toBe(200);
  expect(fallback.headers()["content-type"]).toBe("image/png");
});
