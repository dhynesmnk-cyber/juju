// Server-side only: who is signed in to the owners' section (lib/trackerPass.ts), and their own
// data from the backend, which knows them by `x-juju-user`.
import { cookies } from "next/headers";
import { backendPage } from "@/lib/backend";
import { TRACKER_COOKIE, ownerOf, parseOwners } from "@/lib/trackerPass";

/** The owners' sign-in is set up here (TRACKER_USERS). */
export function trackerOn(): boolean {
  return parseOwners(process.env.TRACKER_USERS) !== null;
}

/** The owner signed in on this device, or null (and null when the section is off). */
export async function currentOwner(): Promise<string | null> {
  const owners = parseOwners(process.env.TRACKER_USERS);
  if (!owners) return null;
  return ownerOf((await cookies()).get(TRACKER_COOKIE)?.value, owners, Date.now());
}

/** An owner's own data: null when there is none (a 404), an error when the backend is out of
 *  reach. A recap works every bet out again, so it gets longer than a page. */
export function meGet<T>(path: string, owner: string): Promise<T | null> {
  return backendPage<T>(`/api/me/${path}`, { "x-juju-user": owner }, 15000);
}
