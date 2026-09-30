"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { TRAY_EVENT, clearTray, parlayHref, readTray, type TrayLeg } from "@/lib/parlayTray";

/** The legs picked so far for this game, pinned to the bottom of the screen. */
export default function ParlayTray({ game }: { game: number }) {
  const [legs, setLegs] = useState<TrayLeg[]>([]);
  useEffect(() => {
    const sync = () => setLegs(readTray(game));
    sync();
    window.addEventListener(TRAY_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(TRAY_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, [game]);
  if (!legs.length) return null;
  return (
    <aside className="tray" aria-label="Parlay">
      <div className="tray-legs">
        <strong>Parlay: {legs.length} {legs.length === 1 ? "leg" : "legs"}</strong>
        <span className="muted small">{legs.map((l) => `${l.who} ${l.bet}`).join(" · ")}</span>
      </div>
      <div className="tray-actions">
        <button type="button" className="share" onClick={() => clearTray(game)}>Clear</button>
        {legs.length >= 2 ? (
          <Link className="share go" href={parlayHref(game, legs.map((l) => l.key).join(","))}>
            See the parlay
          </Link>
        ) : (
          <span className="muted small">Add one more leg</span>
        )}
      </div>
    </aside>
  );
}
