// Server-side only: the backend's private address (Fly's private network in production).
export const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

export async function backendGet<T>(path: string): Promise<T | null> {
  try {
    const r = await fetch(`${BACKEND_URL}${path}`, { cache: "no-store" });
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch {
    return null;
  }
}
