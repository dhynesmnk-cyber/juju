"use client";

import { useEffect, useState } from "react";
import { statusMessage } from "@/lib/status";

/** A visible banner whenever live data is degraded, or new games won't get prices: failures
 *  are never silent. The server renders the first one (so it doesn't push the page down after
 *  load); the browser keeps it current. */
export default function StatusBanner({ initial }: { initial: string | null }) {
  const [message, setMessage] = useState<string | null>(initial);
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const r = await fetch("/api/status");
        if (r.status === 401) {  // a private preview, still locked (proxy.ts): nothing to say
          if (alive) setMessage(null);
          return;
        }
        if (!r.ok) throw new Error();
        if (alive) setMessage(statusMessage(await r.json()));
      } catch {
        if (alive) setMessage("Juju can't reach its live data right now.");
      }
    };
    const t = setInterval(load, 30_000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  if (!message) return null;
  return <div className="banner" role="status">{message}</div>;
}
