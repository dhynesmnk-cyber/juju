"use client";

import { useState } from "react";

/** Share one card: the phone's share sheet where there is one, else copy the link. The link
 *  opens the page with that card first, and its preview is the card's share image. */
export default function ShareButton({ path, text }: { path: string; text: string }) {
  const [said, setSaid] = useState<string | null>(null);
  async function share() {
    const url = new URL(path, window.location.origin).toString();
    try {
      if (navigator.share) {
        await navigator.share({ title: "Juju", text, url });
        return;
      }
      await navigator.clipboard.writeText(url);
      setSaid("Link copied");
    } catch (e) {
      if ((e as Error).name !== "AbortError") setSaid("Couldn't share: copy the address instead");
    }
  }
  return (
    <>
      <button type="button" className="share" onClick={share}>
        <span aria-hidden="true">↗</span> Share
      </button>
      <span className="muted" role="status">{said}</span>
    </>
  );
}
