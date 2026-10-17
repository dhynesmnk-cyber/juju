// Server-side only: the backend's private address (Fly's private network in production).
export const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

type Fetched<T> = { ok: true; data: T } | { ok: false; status: number | null };

async function backendFetch<T>(path: string, timeoutMs: number,
                               headers?: Record<string, string>): Promise<Fetched<T>> {
  try {
    const r = await fetch(`${BACKEND_URL}${path}`, {
      cache: "no-store", signal: AbortSignal.timeout(timeoutMs), headers,
    });
    if (!r.ok) return { ok: false, status: r.status };
    return { ok: true, data: (await r.json()) as T };
  } catch {
    return { ok: false, status: null };  // unreachable, or too slow: never hang a page
  }
}

/** The data, or null for any failure: for things a page can do without. */
export async function backendGet<T>(path: string, timeoutMs = 6000): Promise<T | null> {
  const f = await backendFetch<T>(path, timeoutMs);
  return f.ok ? f.data : null;
}

/** A page's own data: null when it doesn't exist (the page is a 404), and an error when the
 *  backend can't be reached, so the page says that (app/error.tsx) instead of "not found".
 *  `headers`: only the owners' pages pass one (lib/me.ts). */
export async function backendPage<T>(path: string, headers?: Record<string, string>,
                                     timeoutMs = 8000): Promise<T | null> {
  const f = await backendFetch<T>(path, timeoutMs, headers);
  if (f.ok) return f.data;
  if (f.status === 404 || f.status === 422) return null;
  throw new Error(`Juju's data is out of reach (${f.status ?? "no answer"})`);
}
