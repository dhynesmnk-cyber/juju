"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";
import Payout from "@/components/Payout";
import StatusChip, { CASHED, LOST } from "@/components/StatusChip";
import { american } from "@/lib/format";
import {
  TRAY_EVENT, clearTray, legsParam, parlayHref, readTray, removeLeg, type TrayLeg,
} from "@/lib/parlayTray";
import type { ParlayView } from "@/lib/types";

type Priced = { legs: string; view: ParlayView | null };  // null: it couldn't be priced now

/** The parlay slip: the legs picked for this game, one thumb away at the bottom of the screen.
 *  The bar shows how many, and what $10 on them together comes back as; it opens into a sheet
 *  with each leg at the parlay's book. The numbers are the parlay's own endpoint's, asked only
 *  for the legs in the slip (two or more), and the note every parlay carries is always beside
 *  them. The legs themselves stay in the viewer's browser (lib/parlayTray.ts). */
export default function ParlaySlip({ game }: { game: number }) {
  const [legs, setLegs] = useState<TrayLeg[]>([]);
  const [priced, setPriced] = useState<Priced | null>(null);
  const [open, setOpen] = useState(false);
  const sheet = useRef<HTMLDialogElement>(null);
  const sheetId = useId();

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

  const param = legsParam(legs);
  const enough = legs.length >= 2;
  useEffect(() => {
    if (!enough) return;
    const ctl = new AbortController();
    const price = async () => {
      try {
        const r = await fetch(`/api/parlay/${game}?legs=${encodeURIComponent(param)}`,
                              { signal: ctl.signal });
        if (!r.ok) throw new Error();
        setPriced({ legs: param, view: await r.json() });
      } catch {
        if (!ctl.signal.aborted) setPriced({ legs: param, view: null });
      }
    };
    void price();
    return () => ctl.abort();  // a later change of legs wins over a slow answer
  }, [game, param, enough]);

  useEffect(() => {
    const d = sheet.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open, legs.length]);

  if (!legs.length) return null;
  const answer = enough && priced?.legs === param ? priced : null;
  const view = answer?.view ?? null;
  const href = parlayHref(game, param);
  const count = `${legs.length} ${legs.length === 1 ? "leg" : "legs"}`;
  const tone = view && CASHED.has(view.outcome) ? " good" : view && LOST.has(view.outcome) ? " bad" : "";
  const amount = !enough ? "Add one more leg"
    : !answer ? "Pricing it…"
    : !view ? "Can't price it right now"
    : view.returns === null ? view.headline
    : `$${view.back ?? view.returns}`;
  const go = (
    <Link className="btn slip-go" href={href} onClick={() => setOpen(false)}>
      See what it would have paid
    </Link>
  );
  return (
    <aside className="slip" aria-label="Parlay">
      <div className="slip-bar">
        <button type="button" className="slip-summary" aria-haspopup="dialog" aria-expanded={open}
                aria-controls={sheetId} onClick={() => setOpen(true)}>
          <span className="slip-badge num" aria-hidden="true">{legs.length}</span>
          <span className="slip-text">
            <span className="eyebrow">
              Parlay · {count}
            </span>
            <span className={`slip-amount${tone}`}>
              <span className={view?.returns ? "num big" : undefined}>{amount}</span>
              {view?.returns && view.back === null ? " if it hits" : ""}
            </span>
          </span>
          <span className="slip-open" aria-hidden="true">▴</span>
        </button>
        {enough && go}
      </div>
      {view?.notes[0] && (
        <p className="sgp-note">
          {view.book_name ? `Priced at ${view.book_name}. ` : ""}{view.notes[0]}
        </p>
      )}
      <dialog ref={sheet} id={sheetId} className="sheet" aria-label="Parlay slip"
              onClose={() => setOpen(false)}
              onClick={(e) => { if (e.target === e.currentTarget) setOpen(false); }}>
        <div className="sheet-body">
          <div className="sheet-head">
            <h2>Parlay slip</h2>
            <button type="button" className="icon-button" aria-label="Close the slip"
                    onClick={() => setOpen(false)}>✕</button>
          </div>
          <ol className="slip-legs" aria-label={`${count} picked`}>
            {legs.map((l) => {
              const leg = view?.legs.find((x) => x.key === l.key);
              return (
                <li key={l.key}>
                  <div className="slip-leg">
                    <strong>{leg?.who ?? l.who}</strong>
                    <span>{leg?.bet ?? l.bet}</span>
                    {leg && (
                      <span className="slip-leg-meta">
                        <StatusChip outcome={leg.outcome} text={leg.outcome_text} />
                        {leg.price
                          ? <span><span className="num">{american(leg.price.american)}</span> at {leg.price.book_name}</span>
                          : <span>{leg.no_price}</span>}
                      </span>
                    )}
                  </div>
                  <button type="button" className="icon-button"
                          aria-label={`Remove ${l.who}, ${leg?.bet ?? l.bet}`}
                          onClick={() => removeLeg(game, l.key)}>✕</button>
                </li>
              );
            })}
          </ol>
          {view?.returns
            ? <Payout outcome={view.outcome} back={view.back} returns={view.returns} />
            : <p className="payout none">{amount}</p>}
          {view && (
            <ul className="sgp-notes">{view.notes.map((n) => <li key={n}>{n}</li>)}</ul>
          )}
          <div className="sheet-actions">
            {enough ? go : null}
            <button type="button" className="chip-button" onClick={() => clearTray(game)}>
              Clear the slip
            </button>
          </div>
          <p className="hypo-line">Hypothetical. Juju is not a sportsbook.</p>
        </div>
      </dialog>
    </aside>
  );
}
