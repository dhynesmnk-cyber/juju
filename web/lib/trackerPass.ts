// The owners' own sign-in (/me): each owner's name and passcode, from TRACKER_USERS
// ("name:passcode,name:passcode"; backend/juju/config.py reads the same). Off unless it is set,
// and off if it doesn't parse: /me and /api/me are then a 404. Server-side only. No "@/"
// imports: `npm test` runs the lib tests with Node's own test runner.
//
// The cookie is "<name>.<expires>.<mac>", signed with a key derived from that owner's name and
// passcode: it can't be renamed to the other owner, and a new passcode signs out only its owner.
import { passcodeMatches, safeNext } from "./passcode.ts";
import { passIsValid, signPass } from "./signedPass.ts";

export const TRACKER_COOKIE = "juju_tracker";
export const TRACKER_SECONDS = 30 * 24 * 60 * 60;
export const ME_CHECK_PATH = "/me/unlock/check";  // where the sign-in form posts
export const ME_SIGNOUT_PATH = "/me/signout";
const LABEL = "juju-tracker-cookie";
const NAME = /^[a-z][a-z0-9_-]{0,31}$/;
const PASSCODE = /^[A-Za-z0-9._-]{10,200}$/;  // the site passcode's rule (deploy/laptop/up.sh)

export type Owners = ReadonlyMap<string, string>;

export function parseOwners(text: string | undefined): Owners | null {
  if (!text) return null;
  const owners = new Map<string, string>();
  for (const pair of text.split(",")) {
    const [name, code, extra] = pair.trim().split(":");
    if (extra !== undefined || !NAME.test(name ?? "") || !PASSCODE.test(code ?? "")
        || owners.has(name)) return null;
    owners.set(name, code);
  }
  return owners;
}

export function signOwner(name: string, passcode: string, nowMs: number): string {
  return `${name}.${signPass(LABEL, `${name}:${passcode}`, nowMs, TRACKER_SECONDS)}`;
}

/** The owner a cookie was signed for, if it is still good. */
export function ownerOf(cookie: string | undefined, owners: Owners | null,
                        nowMs: number): string | null {
  const dot = cookie?.indexOf(".") ?? -1;
  if (!cookie || !owners || dot < 1) return null;
  const name = cookie.slice(0, dot);
  const passcode = owners.get(name);
  if (!passcode) return null;
  return passIsValid(LABEL, `${name}:${passcode}`, cookie.slice(dot + 1), nowMs) ? name : null;
}

/** Who signed in: the name (any case, spaces around it ignored) and that owner's passcode.
 *  Every passcode is compared, so the time taken says nothing about which names exist. */
export function matchOwner(owners: Owners, name: string, attempt: string): string | null {
  const wanted = name.trim().toLowerCase();
  let found: string | null = null;
  for (const [owner, passcode] of owners) {
    if (passcodeMatches(passcode, attempt) && owner === wanted) found = owner;
  }
  return found;
}

/** "pass": not the owners' section. "off": no owners set, so it isn't there. "open": serve it.
 *  "form": a page, answered with the sign-in form. "locked": an API call, refused. */
export type TrackerGate = "pass" | "off" | "open" | "form" | "locked";

export function trackerGate(path: string, cookie: string | undefined, owners: Owners | null,
                            nowMs: number): TrackerGate {
  const api = path === "/api/me" || path.startsWith("/api/me/");
  if (!api && path !== "/me" && !path.startsWith("/me/")) return "pass";
  if (!owners) return "off";
  if (path === ME_CHECK_PATH || path === ME_SIGNOUT_PATH) return "open";
  if (ownerOf(cookie, owners, nowMs)) return "open";
  return api ? "locked" : "form";
}

/** Where to go once signed in: a path on this site, never the sign-in itself. */
export function safeMeNext(next: string | null | undefined): string {
  const path = safeNext(next);
  return path.startsWith("/me/unlock") || path.startsWith(ME_SIGNOUT_PATH) ? "/me" : path;
}
