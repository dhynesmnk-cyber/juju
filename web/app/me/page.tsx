import { trackerGet, trackerUser } from "@/lib/trackerSession";

type Health = {
  user: string;
  review_count: number;
  banners: { level: "warning" | "error"; text: string }[];
};

// The Watch page arrives with the tracker's pages (docs/plans/integrate-parlaytracker.md, Phase
// 4). Until then this says who is signed in and whether the tracker is healthy.
export default async function TrackerHome() {
  const user = await trackerUser();
  if (!user) {
    return (
      <div className="prose">
        <h1>Watch my parlays</h1>
        <p>Your sign-in has run out. Reload the page to sign in again.</p>
      </div>
    );
  }
  const health = await trackerGet<Health>(user, "health");
  return (
    <div className="prose">
      <h1>Watch my parlays</h1>
      <p>Signed in as <strong>{user}</strong>.</p>
      {health === null ? (
        <p role="status">The tracker isn&apos;t answering right now. Try again in a minute.</p>
      ) : (
        <>
          {health.banners.map((b) => (
            <p key={b.text} role={b.level === "error" ? "alert" : "status"}>{b.text}</p>
          ))}
          {health.review_count > 0 && (
            <p>{health.review_count} {health.review_count === 1 ? "thing needs" : "things need"}
              {" "}your review.</p>
          )}
        </>
      )}
      <p>
        Logging, watching and reviewing your bets move here from ParlayTracker in the next
        update.
      </p>
    </div>
  );
}
