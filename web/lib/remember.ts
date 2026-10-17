import type { Remembered } from "./types";

/** Tell the owners' section a bet page was opened (only rendered for a signed-in owner). Fire
 *  and forget: a page never waits on it, and a failure costs only that one entry. */
export function remember(body: Remembered): void {
  fetch("/api/me/lookups", {
    method: "POST", body: JSON.stringify(body), keepalive: true,
    headers: { "content-type": "application/json" },
  }).catch(() => {});
}
