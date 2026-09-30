"use client";

import { useEffect, useRef, useState } from "react";
import { makeWorld, type Shock, type WorldRenderer } from "./shader";
import { FALLBACK, WORLDS, type World, worldsEnabled } from "./worlds";

const SHOCK_MS = 1400;

/**
 * The world behind the page. The static CSS world paints at once; the shader starts only
 * when the browser is idle after first paint, so it never delays the price (docs/GOALS.md).
 * It steps down in resolution when frames are slow, falls back to CSS if that isn't
 * enough, pauses when the tab is hidden, and keeps to colour fades under reduced motion.
 */
export default function WorldStage({ world, shockAt, shockFrom }: {
  world: World;
  shockAt?: number | null;              // changes each time a shockwave should fire
  shockFrom?: () => DOMRect | null;     // where it starts: the card that cashed
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const target = useRef<World>(world);
  const shock = useRef<{ start: number; x: number; y: number }>({ start: -1, x: 0, y: -0.1 });
  const [renderer, setRenderer] = useState<"css" | "shader">("css");

  useEffect(() => { target.current = world; }, [world]);

  useEffect(() => {
    if (!shockAt || !shockFrom) return;
    const r = shockFrom();
    if (!r) return;
    const h = window.innerHeight, w = window.innerWidth;
    shock.current = {
      start: performance.now(),
      x: ((r.left + r.width / 2) - w / 2) / h,
      y: (h / 2 - (r.top + r.height / 2)) / h,
    };
  }, [shockAt, shockFrom]);

  useEffect(() => {
    if (!worldsEnabled) return;
    const conn = (navigator as Navigator & { connection?: { saveData?: boolean } }).connection;
    if (conn?.saveData) return;
    let gl: WorldRenderer | null = null;
    let raf = 0;
    let cancelled = false;
    const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    const weights = Object.fromEntries(WORLDS.map((k) => [k, k === target.current ? 1 : 0])) as Record<World, number>;
    const s: Shock = { p: 0, x: 0, y: 0 };
    let last = performance.now();
    let slow = 0;

    const loop = (now: number) => {
      raf = requestAnimationFrame(loop);
      if (document.hidden || !gl) return;
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      const reduced = motionQuery.matches;
      const k = 1 - Math.exp(-dt * (reduced ? 5 : 2.4));
      for (const key of WORLDS) weights[key] += ((key === target.current ? 1 : 0) - weights[key]) * k;
      const sh = shock.current;
      s.p = sh.start < 0 || reduced ? 0 : Math.min(1, (now - sh.start) / SHOCK_MS);
      s.x = sh.x; s.y = sh.y;
      if (s.p >= 1) sh.start = -1;
      gl.draw(now / 1000, weights, s, reduced ? 0 : 1);
      if (dt > 0.02) slow += dt; else slow = Math.max(0, slow - dt);
      if (slow > 2) {
        slow = 0;
        if (!gl.lowerQuality()) {   // already at the lowest: give the phone its battery back
          gl.destroy(); gl = null; setRenderer("css");
        }
      }
    };

    const start = () => {
      if (cancelled || !canvasRef.current) return;
      gl = makeWorld(canvasRef.current);
      if (!gl) return;
      setRenderer("shader");
      raf = requestAnimationFrame(loop);
    };
    const onResize = () => gl?.resize();
    window.addEventListener("resize", onResize);
    const idle = window.requestIdleCallback
      ? window.requestIdleCallback(start, { timeout: 1500 })
      : window.setTimeout(start, 300);
    return () => {
      cancelled = true;
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", onResize);
      if (window.cancelIdleCallback) window.cancelIdleCallback(idle as number); else clearTimeout(idle);
      gl?.destroy();
    };
  }, []);

  return (
    <div className="world" aria-hidden="true" data-world={world} data-renderer={renderer}>
      <div className="world-css" style={{ background: FALLBACK[world] }} />
      <canvas ref={canvasRef} className="world-gl" style={{ opacity: renderer === "shader" ? 1 : 0 }} />
    </div>
  );
}
