// `npm test`: Node's own test runner, no network.
import assert from "node:assert/strict";
import { test } from "node:test";
import { CLEARED_SECONDS, passIsValid, signPass, verifyToken } from "./turnstile.ts";

const SECRET = "test-secret";
const NOW = 1_790_000_000_000;

test("a signed pass is valid until it expires, and only with the same secret", () => {
  const pass = signPass(SECRET, NOW);
  assert.equal(passIsValid(SECRET, pass, NOW), true);
  assert.equal(passIsValid(SECRET, pass, NOW + (CLEARED_SECONDS - 1) * 1000), true);
  assert.equal(passIsValid(SECRET, pass, NOW + CLEARED_SECONDS * 1000), false);
  assert.equal(passIsValid("another-secret", pass, NOW), false);
});

test("a forged or malformed pass is refused", () => {
  const [expires, mac] = signPass(SECRET, NOW).split(".");
  const later = String(Number(expires) + 86_400);
  for (const value of [undefined, "", "x", `${later}.${mac}`, `${expires}.${mac}x`,
                       `${expires}.${mac}.more`, `abc.${mac}`, `${expires}.`]) {
    assert.equal(passIsValid(SECRET, value, NOW), false, String(value));
  }
});

function fakeFetch(status: number, body: unknown, seen?: URLSearchParams[]): typeof fetch {
  return (async (_url: string | URL | Request, init?: RequestInit) => {
    seen?.push(init?.body as URLSearchParams);
    return new Response(JSON.stringify(body), { status });
  }) as typeof fetch;
}

test("Cloudflare's answer decides; if it can't be asked, the lookup isn't blocked", async () => {
  const seen: URLSearchParams[] = [];
  assert.equal(await verifyToken(SECRET, "tok", "203.0.113.9",
                                 fakeFetch(200, { success: true }, seen)), "pass");
  assert.equal(seen[0].get("secret"), SECRET);
  assert.equal(seen[0].get("response"), "tok");
  assert.equal(seen[0].get("remoteip"), "203.0.113.9");
  assert.equal(await verifyToken(SECRET, "tok", null, fakeFetch(200, { success: false })),
               "fail");
  assert.equal(await verifyToken(SECRET, "tok", null, fakeFetch(502, {})), "unavailable");
  const down = (async () => { throw new TypeError("fetch failed"); }) as typeof fetch;
  assert.equal(await verifyToken(SECRET, "tok", null, down), "unavailable");
  assert.equal(await verifyToken(SECRET, "", null, fakeFetch(200, { success: true })), "fail");
});
