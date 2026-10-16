"use client";

import Link from "next/link";

/** The backend didn't answer: say so, rather than "not found" (app/not-found.tsx). */
export default function ErrorPage({ reset }: { error: Error; reset: () => void }) {
  return (
    <section className="error-page" role="alert">
      <h1>Juju can&apos;t reach its data right now</h1>
      <p className="muted">
        Saved prices are safe. This usually clears in a minute.
      </p>
      <p><button type="button" className="btn" onClick={() => reset()}>Try again</button></p>
      <p><Link href="/">← Back to the games</Link></p>
    </section>
  );
}
