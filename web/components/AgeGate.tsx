"use client";

import { useSyncExternalStore } from "react";

const KEY = "juju-21-confirmed";

function read(): boolean {
  try {
    return window.localStorage.getItem(KEY) === "yes";
  } catch {
    return false;
  }
}

const listeners = new Set<() => void>();

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

/** One tap, remembered on this device (docs/GOALS.md section 7.8). */
export default function AgeGate() {
  const confirmed = useSyncExternalStore(subscribe, read, () => true);
  if (confirmed) return null;
  const confirm = () => {
    try {
      window.localStorage.setItem(KEY, "yes");
    } catch {
      /* private mode: ask again next time */
    }
    listeners.forEach((l) => l());
  };
  return (
    <div className="gate" role="dialog" aria-modal="true" aria-labelledby="gate-title">
      <div className="panel">
        <h2 id="gate-title">Are you 21 or older?</h2>
        <p>
          Juju shows what past bets would have paid. It is not a sportsbook and takes no bets,
          but it is for adults only.
        </p>
        <div className="actions">
          <button className="btn" onClick={confirm} autoFocus>I am 21 or older</button>
          <a className="btn secondary" href="https://www.ncpgambling.org/" rel="noopener">
            I&apos;m not
          </a>
        </div>
      </div>
    </div>
  );
}
