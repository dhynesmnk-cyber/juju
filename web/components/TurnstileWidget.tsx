"use client";

import { useEffect, useRef } from "react";

// Cloudflare Turnstile, loaded only when a lookup is challenged (docs/deploy.md), so most
// visits never fetch it.
declare global {
  interface Window {
    turnstile?: {
      render: (el: HTMLElement, options: Record<string, unknown>) => string;
      remove: (id: string) => void;
    };
  }
}

export const TURNSTILE_SITE_KEY = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY ?? "";
const SCRIPT = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
let loading: Promise<void> | null = null;

function load(): Promise<void> {
  if (window.turnstile) return Promise.resolve();
  loading ??= new Promise<void>((resolve, reject) => {
    const s = document.createElement("script");
    s.src = SCRIPT;
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => { loading = null; reject(new Error("Turnstile didn't load")); };
    document.head.appendChild(s);
  });
  return loading;
}

/** `onToken` and `onError` must keep their identity (useCallback), or the widget restarts. */
export default function TurnstileWidget({ onToken, onError }: {
  onToken: (token: string) => void; onError: () => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let id: string | undefined;
    let gone = false;
    load().then(() => {
      if (gone || !box.current || !window.turnstile) return;
      id = window.turnstile.render(box.current, {
        sitekey: TURNSTILE_SITE_KEY, callback: onToken, "error-callback": onError,
        theme: "auto", size: "flexible",
      });
    }).catch(onError);
    return () => {
      gone = true;
      if (id && window.turnstile) window.turnstile.remove(id);
    };
  }, [onToken, onError]);
  return <div ref={box} className="turnstile" />;
}
