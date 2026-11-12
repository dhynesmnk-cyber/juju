"use client";

import Link from "next/link";
import { fillTray, legsParam, parlayHref } from "@/lib/parlayTray";
import { quickPicks } from "@/lib/quickPicks";
import type { GamePageView } from "@/lib/types";

/** A few ready-made parlays for this game, built here from its players and plays
 *  (lib/quickPicks.ts). The list holds names and bets in words and no price: opening one fills
 *  the slip and goes to the parlay's own page, which is where its prices are first asked for.
 *  So the links are never prefetched. */
export default function QuickPicks({ game }: { game: GamePageView }) {
  const picks = quickPicks(game);
  if (!picks.length) return null;
  return (
    <section className="quick" aria-labelledby="quick-title">
      <h2 id="quick-title">Quick picks</h2>
      <p className="page-note">
        Ready-made parlays from this game&apos;s plays and players. No prices until you open one.
      </p>
      <ul className="quick-list">
        {picks.map((p) => (
          <li key={p.id}>
            <Link className="quick-card" href={parlayHref(game.id, legsParam(p.legs))}
                  prefetch={false} onClick={() => fillTray(game.id, p.legs)}>
              <span className="eyebrow">{p.legs.length}-leg parlay</span>
              <strong>{p.title}</strong>
              <span className="quick-about">{p.about}</span>
              <span className="quick-go">
                See what it would have paid <span aria-hidden="true">›</span>
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
