"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { scoreline, totalsLine } from "@/lib/format";
import { recapHref } from "@/lib/links";
import type { RecapGame } from "@/lib/types";

/** On the home page, for a signed-in owner only: the recaps of games that just finished.
 *  Loaded in the browser, so the public home page is the same for everyone. */
export default function MyRecaps() {
  const [games, setGames] = useState<RecapGame[]>([]);
  useEffect(() => {
    let alive = true;
    fetch("/api/me/recaps?recent=1")
      .then((r) => (r.ok ? r.json() : { games: [] }))
      .then((body: { games: RecapGame[] }) => { if (alive) setGames(body.games); })
      .catch(() => {});
    return () => { alive = false; };
  }, []);
  if (!games.length) return null;
  return (
    <section className="my-recaps" aria-label="Your recaps">
      <div className="tiles">
        {games.map(({ game, lookups, totals }) => (
          <Link key={game.id} className="tile small" href={recapHref(game.id)} prefetch={false}>
            <strong>Your recap: {scoreline(game)}</strong>
            <span className="muted">
              {lookups} {lookups === 1 ? "lookup" : "lookups"}
              {totals ? ` · ${totalsLine(totals)}` : ""}
            </span>
          </Link>
        ))}
      </div>
    </section>
  );
}
