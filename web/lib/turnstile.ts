// Cloudflare Turnstile for lookups past the backend's threshold (docs/deploy.md). Server-side
// only. No "@/" imports: `npm test` runs lib/turnstile.test.ts with Node's own test runner.
//
// A person who passes the check gets a signed cookie and isn't asked again for an hour. The
// cookie says only when it expires; it is signed with a key derived from the Turnstile secret,
// so the site needs no other secret and no shared store.
import { createHmac, timingSafeEqual } from "node:crypto";

export const COOKIE = "juju_ok";
export const CLEARED_SECONDS = 60 * 60;
export const VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify";

export type Verdict = "pass" | "fail" | "unavailable";

function macOf(secret: string, expires: string): Buffer {
  const key = createHmac("sha256", "juju-turnstile-cookie").update(secret).digest();
  return createHmac("sha256", key).update(expires).digest();
}

export function signPass(secret: string, nowMs: number): string {
  const expires = String(Math.floor(nowMs / 1000) + CLEARED_SECONDS);
  return `${expires}.${macOf(secret, expires).toString("base64url")}`;
}

export function passIsValid(secret: string, value: string | undefined, nowMs: number): boolean {
  const [expires, mac, extra] = (value ?? "").split(".");
  if (!expires || !mac || extra !== undefined || !/^\d{1,12}$/.test(expires)) return false;
  if (Number(expires) * 1000 <= nowMs) return false;
  const want = macOf(secret, expires);
  const got = Buffer.from(mac, "base64url");
  return got.length === want.length && timingSafeEqual(got, want);
}

/** Ask Cloudflare about a widget's token. "fail": Cloudflare said no. "unavailable": Cloudflare
 *  couldn't be asked; the lookup goes through (the per-minute limit still holds) rather than
 *  locking people out while Cloudflare is down. */
export async function verifyToken(secret: string, token: string, ip: string | null,
                                  fetchImpl: typeof fetch = fetch,
                                  url: string = VERIFY_URL): Promise<Verdict> {
  if (!token || token.length > 2048) return "fail";
  const body = new URLSearchParams({ secret, response: token });
  if (ip && ip !== "unknown") body.set("remoteip", ip);
  try {
    const r = await fetchImpl(url, { method: "POST", body, signal: AbortSignal.timeout(5000) });
    if (!r.ok) return "unavailable";
    const data = (await r.json()) as { success?: unknown };
    return data.success === true ? "pass" : "fail";
  } catch {
    return "unavailable";
  }
}
