"use client";

import { useEffect, useState } from "react";
import { MAX_LEGS, TRAY_EVENT, readTray, toggleLeg, type TrayLeg } from "@/lib/parlayTray";

/** Add a card to this game's parlay tray, or take it out. */
export default function ParlayButton({ game, leg }: { game: number; leg: TrayLeg }) {
  const [inTray, setInTray] = useState(false);
  const [full, setFull] = useState(false);
  useEffect(() => {
    const sync = () => {
      const legs = readTray(game);
      setInTray(legs.some((l) => l.key === leg.key));
      setFull(legs.length >= MAX_LEGS);
    };
    sync();
    window.addEventListener(TRAY_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(TRAY_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, [game, leg.key]);
  return (
    <button type="button" className="share" aria-pressed={inTray} disabled={!inTray && full}
            onClick={() => toggleLeg(game, leg)}>
      <span aria-hidden="true">{inTray ? "✓" : "+"}</span>{" "}
      {inTray ? "In parlay" : full ? `Parlay full (${MAX_LEGS})` : "Add to parlay"}
    </button>
  );
}
