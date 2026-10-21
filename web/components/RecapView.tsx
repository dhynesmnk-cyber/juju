"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import MiniCard, { ParlayMini } from "@/components/MiniCard";
import { quarter, scoreline, totalsLine } from "@/lib/format";
import { lookupHref } from "@/lib/links";
import type { Recap, RecapBet, RecapLookup, RecapState } from "@/lib/types";

// After the game's own status ("Final", "Q3 4:12"): what that means for this recap.
const STATE_TEXT: Record<RecapState, string> = {
  pregame: "not started yet",
  in_play: "this recap updates as the game goes",
  final: "checked against the official stats the next morning",
  checked: "checked against the official stats",
  off: "called off",
};

function decided(b: RecapBet): string | null {
  const d = b.decided_by;
  if (!d) return null;
  const when = [quarter(d.period), d.clock].filter(Boolean).join(" ");
  return d.text ? `Decided by: ${d.text}${when ? ` (${when})` : ""}` : null;
}

/** What to save again to undo a remove: the page as it was opened. */
function asOpened(l: RecapLookup, gameId: number) {
  return { kind: l.kind, game_id: gameId, target: l.target, play: l.play, market: l.market,
           threshold: l.threshold, yards: l.yards, expect: l.expect, play_id: l.play_id };
}

/** One game's recap for the owner signed in: each bet page they opened, the bets it was about
 *  and how they went, worked out again from the archive (never stored); and the totals. */
export default function RecapView({ initial }: { initial: Recap }) {
  const [recap, setRecap] = useState<Recap | null>(initial);
  const [undo, setUndo] = useState<RecapLookup | null>(null);
  const [failed, setFailed] = useState(false);
  const game = initial.game;

  const reload = useCallback(async () => {
    try {
      const r = await fetch(`/api/me/recaps/${game.id}`);
      if (r.status === 404) { setRecap(null); return; }  // nothing left in it
      if (!r.ok) throw new Error();
      setRecap(await r.json());
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, [game.id]);

  useEffect(() => {
    if (recap?.state !== "in_play") return;
    const t = setInterval(reload, 30_000);
    return () => clearInterval(t);
  }, [recap?.state, reload]);

  const removeIt = async (l: RecapLookup) => {
    setRecap((r) => r && { ...r, lookups: r.lookups.filter((x) => x.id !== l.id) });
    setUndo(l);
    await fetch(`/api/me/lookups/${l.id}`, { method: "DELETE" }).catch(() => {});
    await reload();
  };

  const undoIt = async () => {
    if (!undo) return;
    const l = undo;
    setUndo(null);
    await fetch("/api/me/lookups", {
      method: "POST", body: JSON.stringify(asOpened(l, game.id)),
      headers: { "content-type": "application/json" },
    }).catch(() => {});
    await reload();
  };

  const g = recap?.game ?? game;
  return (
    <>
      <div className="game-head">
        <Link className="back" href="/me">‹ Your lookups</Link>
        <h1 className="scoreline">{scoreline(g)}</h1>
        <p className="sub">{g.status_text} · {STATE_TEXT[recap?.state ?? initial.state]}</p>
      </div>
      {failed && <p className="hint" role="alert">Can&apos;t refresh right now.</p>}
      {recap === null || recap.lookups.length === 0 ? (
        <p className="muted">Nothing left in this recap.</p>
      ) : (
        <>
          <p className="totals" aria-live="polite">{totalsLine(recap.totals)}</p>
          <ol className="recap-list">
            {recap.lookups.map((l) => (
              <li key={l.id}>
                <div className="recap-head">
                  <div>
                    <strong>{l.who ?? "A page that's gone"}</strong>
                    {l.asked && <span className="muted"> · {l.asked}</span>}
                  </div>
                  <button type="button" className="link-button" onClick={() => removeIt(l)}
                          aria-label={`Remove ${l.who ?? "this lookup"}${l.asked ? `, ${l.asked}` : ""}`}>
                    Remove
                  </button>
                </div>
                {l.parlay && <ParlayMini p={l.parlay} />}
                {l.bets.map((b) => (
                  <MiniCard key={`${b.key}-${b.line}`} c={b} hypo={false}>
                    {decided(b) && <div className="detail">{decided(b)}</div>}
                    {b.verified && <div className="detail">Checked against the official stats.</div>}
                  </MiniCard>
                ))}
                {!l.parlay && l.bets.length === 0 && (
                  <p className="muted small">{l.no_price ?? "No price on file for this."}</p>
                )}
                {l.unpriced > 0 && (
                  <p className="muted small">
                    {l.unpriced} more {l.unpriced === 1 ? "bet" : "bets"} it touched had no price on file.
                  </p>
                )}
                <Link className="recap-open" href={lookupHref(asOpened(l, game.id))} prefetch={false}>
                  Open the page <span aria-hidden="true">›</span>
                </Link>
              </li>
            ))}
          </ol>
        </>
      )}
      {undo && (
        <p className="undo" role="status">
          Removed {undo.who ?? "it"}{undo.asked ? `, ${undo.asked}` : ""}.{" "}
          <button type="button" className="link-button" onClick={undoIt}>Undo</button>
        </p>
      )}
      <p className="hypo-line">
        Hypothetical: what $10 at the archived price would have returned. Juju is not a
        sportsbook, and none of this is a tip.
      </p>
    </>
  );
}
