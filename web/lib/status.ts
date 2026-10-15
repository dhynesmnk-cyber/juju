/** What the site's banner says, from the backend's /api/status: live data degraded, or new
 *  games not getting prices. Shared by the server's first render and the browser's polling. */
export interface StatusBody {
  degraded: boolean;
  message: string | null;
  prices?: string;
  prices_message?: string | null;
}

export function statusMessage(body: StatusBody | null): string | null {
  if (!body) return null;
  const said = [body.degraded ? body.message : null, body.prices_message ?? null]
    .filter(Boolean).join(" ");
  return said || null;
}
