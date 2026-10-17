"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import MiniCard from "@/components/MiniCard";
import { etDay, scoreline, totalsLine } from "@/lib/format";
import { recapHref } from "@/lib/links";
import type { History } from "@/lib/types";

/** The signed-in owner's own earlier lookups of this player or team, and how they went: in the
 *  games before this one, latest first. Loaded after the page, so the price always shows
 *  first; it says nothing at all if it can't load. */
export default function MyHistory({ kind, id, gameId, name }: {
  kind: "player" | "team"; id: string; gameId: number; name: string;
}) {
  const [history, setHistory] = useState<History | null>(null);
  useEffect(() => {
    let alive = true;
    fetch(`/api/me/history/${kind}/${id}?before_game=${gameId}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((h: History | null) => { if (alive) setHistory(h); })
      .catch(() => {});
    return () => { alive = false; };
  }, [kind, id, gameId]);
  if (!history) return null;
  return (
    <section className="my-history" aria-labelledby="my-history-title">
      <p className="eyebrow">Only you see this</p>
      <h2 id="my-history-title">Your earlier lookups of {name}</h2>
      {history.games.length === 0 ? (
        <p className="muted">No earlier lookups of {name}. After this game, this one shows here.</p>
      ) : (
        <>
          <p className="totals">{totalsLine(history.totals)}</p>
          {history.games.map(({ game, bets, unpriced }) => (
            <div key={game.id} className="history-game">
              <h3>
                <Link href={recapHref(game.id)} prefetch={false}>
                  {scoreline(game)}
                </Link>
                <span className="muted small"> · {etDay(game.commence_time)}</span>
              </h3>
              {bets.map((b) => <MiniCard key={`${b.key}-${b.line}`} c={b} hypo={false} />)}
              {bets.length === 0 && (
                <p className="muted small">
                  {unpriced ? "No price on file for what you looked up." : "No priced bets."}
                </p>
              )}
            </div>
          ))}
          <p className="hypo-line">Hypothetical: what $10 at the archived price would have returned.</p>
        </>
      )}
    </section>
  );
}
