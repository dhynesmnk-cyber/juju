import { expect, test } from "@playwright/test";

// Needs the site built and started with Cloudflare's public test keys (CI sets them):
// NEXT_PUBLIC_TURNSTILE_SITE_KEY=1x00000000000000000000AA,
// TURNSTILE_SECRET_KEY=1x0000000000000000000000000000000AA, and TURNSTILE_E2E=1 here.
// The widget itself is a stand-in, so the browser never reaches Cloudflare.
const FAKE_WIDGET = `window.turnstile = {
  render(el, o) {
    const b = document.createElement("button");
    b.type = "button"; b.textContent = "Verify you are human";
    b.onclick = () => o.callback("XXXX.DUMMY.TOKEN.XXXX");
    el.appendChild(b);
    return "w1";
  },
  remove() {},
};`;

test("past the threshold a lookup asks for a check, then goes through", async ({ page }) => {
  test.skip(!process.env.TURNSTILE_E2E, "needs the site built with Turnstile test keys");
  // A client of its own, so no other test's lookups count towards its threshold.
  await page.setExtraHTTPHeaders({ "x-forwarded-for": `198.51.100.${1 + Date.now() % 200}` });
  await page.route("https://challenges.cloudflare.com/turnstile/v0/api.js*",
                   (route) => route.fulfill({ contentType: "text/javascript", body: FAKE_WIDGET }));
  await page.addInitScript(() => window.localStorage.setItem("juju-21-confirmed", "yes"));
  await page.goto("/");
  for (let i = 0; i < 12; i++) {  // the free lookups (CHALLENGE_AFTER in the API)
    const status = await page.evaluate(async () => (await fetch("/api/lookup", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ text: "Barkley" }),
    })).status);
    expect(status).toBe(200);
  }
  await page.getByRole("combobox").fill("Barkley 17 yd run");
  await page.getByRole("button", { name: "Look it up" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Quick check" })).toBeVisible();
  await page.getByRole("button", { name: "Verify you are human" }).click();
  await expect(page.getByRole("heading", { name: "Saquon Barkley" })).toBeVisible();
});
