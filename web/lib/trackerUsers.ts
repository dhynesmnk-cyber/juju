// "Watch my parlays" (/me) is for its users only: TRACKER_USERS="Chris:<passcode>,Ash:<passcode>",
// each with a passcode of their own (docs/plans/integrate-parlaytracker.md section 7). Off unless
// set. Server-side only. No "@/" imports: `npm test` runs the lib tests with Node's own test runner.
//
// Signing in sets a cookie, "<name>.<expires>.<mac>", good for 30 days on that device. Its key is
// derived from that user's passcode and name, so changing one user's passcode signs out only that
// user, and the site needs no other secret.
import { createHash, timingSafeEqual } from "node:crypto";
import { passIsValid, signPass } from "./signedPass.ts";

export const TRACKER_COOKIE = "juju_tracker";
export const TRACKER_SECONDS = 30 * 24 * 60 * 60;
export const TRACKER_CHECK_PATH = "/me/unlock/check";  // where the sign-in form posts
// The site is on the public internet behind these (deploy/laptop/up.sh refuses shorter ones too).
export const MIN_PASSCODE = 10;
const LABEL = "juju-tracker-cookie";
// A name is `logged_by` on a slip and sits before the cookie's first dot, so it has none
// (backend/juju/config.py has the same rule).
const NAME = /^[A-Za-z][A-Za-z0-9_-]{0,31}$/;

export type TrackerUser = { name: string; passcode: string };

/** The users TRACKER_USERS names. An entry with a bad name, a passcode under MIN_PASSCODE
 *  characters or a name already taken is left out, so it can never sign in; `problems` says
 *  which, by position only (a missing colon would put the passcode in the name). */
export function parseTrackerUsers(raw: string | undefined): {
  users: TrackerUser[]; problems: string[];
} {
  const users: TrackerUser[] = [];
  const problems: string[] = [];
  (raw ?? "").split(",").map((entry) => entry.trim()).filter(Boolean).forEach((entry, i) => {
    const colon = entry.indexOf(":");
    const name = (colon < 0 ? entry : entry.slice(0, colon)).trim();
    const passcode = colon < 0 ? "" : entry.slice(colon + 1).trim();
    const where = `TRACKER_USERS entry ${i + 1}`;
    if (!NAME.test(name)) {
      problems.push(`${where}: a name is a letter, then letters, digits, - or _`);
    } else if (passcode.length < MIN_PASSCODE) {
      problems.push(`${where}: the passcode needs at least ${MIN_PASSCODE} characters`);
    } else if (users.some((u) => u.name.toLowerCase() === name.toLowerCase())) {
      problems.push(`${where}: that name is already taken`);
    } else {
      users.push({ name, passcode });
    }
  });
  return { users, problems };
}

/** /me and everything under it, and the API behind it. */
export function isTrackerPath(path: string): boolean {
  return path === "/me" || path.startsWith("/me/") || path === "/api/me"
    || path.startsWith("/api/me/");
}

const label = (name: string) => `${LABEL}:${name}`;

export function trackerPass(user: TrackerUser, nowMs: number): string {
  return `${user.name}.${signPass(label(user.name), user.passcode, nowMs, TRACKER_SECONDS)}`;
}

/** The signed-in user's name, or null: no cookie, someone else's, expired, or a passcode that
 *  has changed since. */
export function trackerUserFrom(cookie: string | undefined, users: TrackerUser[],
                                nowMs: number): string | null {
  const value = cookie ?? "";
  const dot = value.indexOf(".");
  if (dot < 1) return null;
  const user = users.find((u) => u.name === value.slice(0, dot));
  if (!user) return null;
  return passIsValid(label(user.name), user.passcode, value.slice(dot + 1), nowMs)
    ? user.name : null;
}

/** "open": serve it. "off": no users, so /me doesn't exist (a 404). "form": a page, answered
 *  with the sign-in form (lib/unlockPage.ts). "locked": an API call, refused with a 401. */
export type TrackerGate = "open" | "off" | "form" | "locked";

export function trackerGate(path: string, cookie: string | undefined, users: TrackerUser[],
                            nowMs: number): TrackerGate {
  if (!isTrackerPath(path)) return "open";
  if (users.length === 0) return "off";
  if (path === TRACKER_CHECK_PATH) return "open";
  if (trackerUserFrom(cookie, users, nowMs)) return "open";
  return path.startsWith("/api/") ? "locked" : "form";
}

const digest = (text: string) => createHash("sha256").update(text).digest();

/** Who this name and passcode belong to, or null. Every user is compared, in constant time,
 *  so how long it takes says nothing about which name exists. */
export function signIn(users: TrackerUser[], name: string, passcode: string): TrackerUser | null {
  const typedName = digest(name.trim().toLowerCase());
  const typedCode = digest(passcode.trim());
  let found: TrackerUser | null = null;
  for (const user of users) {
    const nameOk = timingSafeEqual(digest(user.name.toLowerCase()), typedName);
    const codeOk = timingSafeEqual(digest(user.passcode), typedCode);
    if (nameOk && codeOk) found = user;
  }
  return found;
}

/** Where to go once signed in: a page under /me, never the form itself or another site. */
export function trackerNext(next: string | null | undefined): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || /[\\\u0000-\u001f]/.test(next)) {
    return "/me";
  }
  const path = next.split(/[?#]/)[0];
  if (!(path === "/me" || path.startsWith("/me/")) || path.startsWith("/me/unlock")) return "/me";
  return next;
}
