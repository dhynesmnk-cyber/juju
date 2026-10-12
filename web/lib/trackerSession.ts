// Server-side only: who is signed in to "Watch my parlays", for the /me pages, and their reads of
// the tracker's API. The rules themselves are in lib/trackerUsers.ts.
import { cookies } from "next/headers";
import { BACKEND_URL } from "@/lib/backend";
import { TRACKER_COOKIE, parseTrackerUsers, trackerUserFrom } from "@/lib/trackerUsers";

export function trackerEnabled(): boolean {
  return parseTrackerUsers(process.env.TRACKER_USERS).users.length > 0;
}

/** The signed-in user's name. proxy.ts sends anyone else the sign-in form before a /me page
 *  renders, so null here means the cookie expired in between. */
export async function trackerUser(): Promise<string | null> {
  const { users } = parseTrackerUsers(process.env.TRACKER_USERS);
  return trackerUserFrom((await cookies()).get(TRACKER_COOKIE)?.value, users, Date.now());
}

/** GET /api/me/<path> on the backend as `user`; null if it fails. */
export async function trackerGet<T>(user: string, path: string): Promise<T | null> {
  try {
    const r = await fetch(`${BACKEND_URL}/api/me/${path}`, {
      cache: "no-store", headers: { "x-juju-user": user }, signal: AbortSignal.timeout(8000),
    });
    return r.ok ? ((await r.json()) as T) : null;
  } catch {
    return null;
  }
}
