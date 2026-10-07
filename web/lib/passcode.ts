// A shared passcode in front of the whole site, for a private preview (deploy/laptop/). Off
// unless SITE_PASSCODE is set. Server-side only. No "@/" imports: `npm test` runs the lib tests
// with Node's own test runner.
//
// Entering it sets a cookie good for 30 days on that device. The cookie is signed with a key
// derived from the passcode itself, so changing the passcode signs everyone out and the site
// needs no other secret.
import { createHash, timingSafeEqual } from "node:crypto";
import { passIsValid, signPass } from "./signedPass.ts";

export const PASS_COOKIE = "juju_pass";
export const PASS_SECONDS = 30 * 24 * 60 * 60;
export const CHECK_PATH = "/unlock/check";  // where the passcode form posts
const LABEL = "juju-passcode-cookie";

/** "open": serve it. "form": a page, answered with the passcode form (lib/unlockPage.ts).
 *  "locked": an API call, refused with a 401 the page's own fetches can tell from an outage. */
export type Gate = "open" | "form" | "locked";

export function gate(path: string, cookie: string | undefined, passcode: string | undefined,
                     nowMs: number): Gate {
  if (!passcode || path === CHECK_PATH) return "open";
  if (passIsValid(LABEL, passcode, cookie, nowMs)) return "open";
  return path === "/api" || path.startsWith("/api/") ? "locked" : "form";
}

export function unlockPass(passcode: string, nowMs: number): string {
  return signPass(LABEL, passcode, nowMs, PASS_SECONDS);
}

export function passcodeMatches(passcode: string, attempt: string): boolean {
  const digest = (text: string) => createHash("sha256").update(text).digest();
  return timingSafeEqual(digest(passcode), digest(attempt.trim()));
}

/** Where to go once unlocked: a path on this site, never another site or the passcode form. */
export function safeNext(next: string | null | undefined): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || /[\\\u0000-\u001f]/.test(next)) {
    return "/";
  }
  return next.startsWith("/unlock") ? "/" : next;
}
