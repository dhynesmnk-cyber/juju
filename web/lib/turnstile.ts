// Cloudflare Turnstile for lookups past the backend's threshold (docs/deploy.md). Server-side
// only. No "@/" imports: `npm test` runs lib/turnstile.test.ts with Node's own test runner.
//
// A person who passes the check gets a signed cookie and isn't asked again for an hour. The
// cookie says only when it expires; it is signed with a key derived from the Turnstile secret,
// so the site needs no other secret and no shared store.
import * as signed from "./signedPass.ts";

export const COOKIE = "juju_ok";
export const CLEARED_SECONDS = 60 * 60;
export const VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify";
const LABEL = "juju-turnstile-cookie";

export type Verdict = "pass" | "fail" | "unavailable";

export function signPass(secret: string, nowMs: number): string {
  return signed.signPass(LABEL, secret, nowMs, CLEARED_SECONDS);
}

export function passIsValid(secret: string, value: string | undefined, nowMs: number): boolean {
  return signed.passIsValid(LABEL, secret, value, nowMs);
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
