"use client";

import { useEffect, useState } from "react";
import type { Credits, MarketItem } from "@/lib/liveChat";

type State =
  | { status: "loading" }
  | { status: "ok"; markets: MarketItem[]; cached: boolean }
  | { status: "error"; message: string };

/** Which bets the books post for this game: names only, no prices. Asking costs 1 credit (The
 *  Odds API), kept five minutes, and is asked once when the game is opened. */
export default function MarketsPanel({ gameId, onCredits }: {
  gameId: string;
  onCredits: (c: Credits) => void;
}) {
  const [state, setState] = useState<State>({ status: "loading" });

  useEffect(() => {
    const ctl = new AbortController();
    (async () => {
      try {
        const r = await fetch(`/api/live/markets?game_id=${encodeURIComponent(gameId)}`,
                              { signal: ctl.signal });
        const body = await r.json().catch(() => ({}));
        if (body.credits) onCredits(body.credits);
        if (r.ok && Array.isArray(body.markets)) {
          setState({ status: "ok", markets: body.markets, cached: Boolean(body.cached) });
        } else {
          setState({ status: "error",
                     message: body.message ?? "Couldn't list this game's bets right now." });
        }
      } catch {
        if (!ctl.signal.aborted) {
          setState({ status: "error", message: "Couldn't list this game's bets right now." });
        }
      }
    })();
    return () => ctl.abort();
  }, [gameId, onCredits]);

  const markets = state.status === "ok" ? state.markets : [];
  const lines = markets.filter((m) => m.scope !== "player");
  const props = markets.filter((m) => m.scope === "player" && !m.alternate);
  const ladders = markets.filter((m) => m.scope === "player" && m.alternate);

  return (
    <details className="markets" open>
      <summary>
        What&apos;s posted for this game
        {state.status === "ok" && <span className="muted">({markets.length})</span>}
      </summary>
      {state.status === "loading" && <p className="muted small" role="status">Listing the bets…</p>}
      {state.status === "error" && (
        <p className="small" role="status">
          {state.message} You can still ask: a bet the books don&apos;t post will say so.
        </p>
      )}
      {state.status === "ok" && markets.length === 0 && (
        <p className="small" role="status">
          No book is posting any bets for this game right now.
        </p>
      )}
      {[["Game lines", lines], ["Player props", props], ["Other lines (alternates)", ladders]]
        .map(([title, items]) => (items as MarketItem[]).length > 0 && (
          <div key={title as string}>
            <p className="eyebrow">{title as string}</p>
            <ul className="market-chips">
              {(items as MarketItem[]).map((m) => <li key={m.key}>{m.label}</li>)}
            </ul>
          </div>
        ))}
      {state.status === "ok" && (
        <p className="muted small">
          {state.cached ? "From the last five minutes. " : ""}
          A player can be missing from a bet the game offers: ask and Juju will say so.
        </p>
      )}
    </details>
  );
}
