"use client";

import { useEffect, useSyncExternalStore } from "react";

const KEY = "juju-21-confirmed";
export const AGE_COOKIE = "juju21";
const YEAR = 60 * 60 * 24 * 365;

function hasCookie(): boolean {
  return document.cookie.split("; ").includes(`${AGE_COOKIE}=yes`);
}

function read(): boolean {
  try {
    return hasCookie() || window.localStorage.getItem(KEY) === "yes";
  } catch {
    return false;
  }
}

/** Remembered in a cookie, so the server knows and never draws the gate after the page;
 *  and in localStorage, as before. */
function remember() {
  document.cookie = `${AGE_COOKIE}=yes; Max-Age=${YEAR}; Path=/; SameSite=Lax`;
  try {
    window.localStorage.setItem(KEY, "yes");
  } catch {
    /* private mode: the cookie still holds for this visit */
  }
}

const listeners = new Set<() => void>();

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

/** One tap, remembered on this device (docs/GOALS.md section 7.8). `confirmed` is what the
 *  server saw in the cookie. */
export default function AgeGate({ confirmed: onServer }: { confirmed: boolean }) {
  const confirmed = useSyncExternalStore(subscribe, read, () => onServer);
  useEffect(() => {
    if (confirmed && !hasCookie()) remember();  // a "yes" from before the cookie
  }, [confirmed]);
  if (confirmed) return null;
  const confirm = () => {
    remember();
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
