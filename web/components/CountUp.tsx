"use client";

import { useEffect, useRef, useState } from "react";

/** "$10 → $19.52", counting up from $10 when `run` is set. It only ever counts towards the
 *  number the API sent, and always ends exactly on it. The server renders the final number
 *  (so the page is right without JavaScript); the count runs in the browser only. Reduced
 *  motion shows the final number at once. */
export default function CountUp({ to, run }: { to: string; run: boolean }) {
  const target = Number(to);
  const [shown, setShown] = useState(to);
  const done = useRef(!run);
  useEffect(() => {
    if (!run || done.current || !Number.isFinite(target)) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      done.current = true;
      return;
    }
    const t0 = performance.now();
    let raf = 0;
    const step = (now: number) => {
      const k = Math.min(1, (now - t0) / 1100);
      const e = 1 - Math.pow(1 - k, 3);
      if (k >= 1) { done.current = true; setShown(to); return; }
      setShown((10 + (target - 10) * e).toFixed(2));
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [run, target, to]);
  return <span className="count" data-final={to}>$10 → ${shown}</span>;
}
