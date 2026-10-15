"use client";

import { useEffect, useState } from "react";

/** A visible banner whenever live data is degraded, or new games won't get prices: failures
 *  are never silent. */
export default function StatusBanner() {
  const [message, setMessage] = useState<string | null>(null);
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
        const body = await r.json();
        const said = [body.degraded ? body.message : null, body.prices_message ?? null]
          .filter(Boolean).join(" ");
        if (alive) setMessage(said || null);
      } catch {
        if (alive) setMessage("Juju can't reach its live data right now.");
      }
    };
    load();
    const t = setInterval(load, 30_000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  if (!message) return null;
  return <div className="banner" role="status">{message}</div>;
}
