import { expect, test } from "@playwright/test";

// "Watch my parlays" (/me). Needs the site and the API both started with TRACKER_USERS, and the
// same value here (CI sets a test user); without it, /me doesn't exist and this is skipped.
const [NAME, PASSCODE] = (process.env.TRACKER_USERS ?? "").split(",")[0].split(/:(.*)/s);

test.beforeEach(() => {
  test.skip(!PASSCODE, "needs TRACKER_USERS, as the site and the API were started with");
});

test("the tracker's API is locked until someone signs in", async ({ request }) => {
  const r = await request.get("/api/me/health");
  expect(r.status()).toBe(401);
  expect(await r.json()).toMatchObject({ locked: true });
  expect(r.headers()["cache-control"]).toBe("no-store");
});

test("signing in opens /me as that user; a wrong passcode says so", async ({ page }) => {
  await page.addInitScript(() => window.localStorage.setItem("juju-21-confirmed", "yes"));
  await page.goto("/me");
  await expect(page.getByRole("heading", { name: "Watch my parlays" })).toBeVisible();
  await page.getByLabel("Name").fill(NAME);
  await page.getByLabel("Passcode").fill("not-the-passcode");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toContainText("don't match");

  await page.getByLabel("Name").fill(NAME.toLowerCase());
  await page.getByLabel("Passcode").fill(PASSCODE);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/me$/);
  await expect(page.getByText(`Signed in as ${NAME}.`)).toBeVisible();

  // As the /me pages will ask: from the page, with its cookie (Playwright's own request client
  // won't send a Secure cookie to http://127.0.0.1).
  const health = await page.evaluate(async () => {
    const r = await fetch("/api/me/health");
    return { status: r.status, cache: r.headers.get("cache-control"), body: await r.json() };
  });
  expect(health).toMatchObject({ status: 200, cache: "no-store", body: { user: NAME } });
});
