import Link from "next/link";
import CreditsMeter from "@/components/CreditsMeter";
import LocalTime from "@/components/LocalTime";
import ThreadTidy from "@/components/ThreadTidy";
import { backendGet } from "@/lib/backend";
import { byEtDay } from "@/lib/format";
import type { ChatGame, Credits } from "@/lib/liveChat";

interface GamesBody {
  games: ChatGame[];
  credits: Credits;
}

/** The front page when the live chat is on: pick a game from the live schedule. The schedule is
 *  free to list (The Odds API charges nothing for it), and shows no prices. */
export default async function LiveHome() {
  const body = await backendGet<GamesBody>("/api/live/games");
  return (
    <>
      <h1 className="home-title">Live odds, on demand</h1>
      <p className="tagline">
        Pick a game, then ask about one player or team: <em>&ldquo;Fannin got 4 catches&rdquo;</em>.
        You get the price a book posts right now, in plain English.
      </p>
      {body === null ? (
        <p className="empty" role="alert">
          Live odds aren&apos;t available right now: the schedule couldn&apos;t be loaded.
          The <Link href="/archive">archive</Link> still works.
        </p>
      ) : (
        <>
          <CreditsMeter credits={body.credits} />
          <ThreadTidy onSchedule={body.games.map((g) => g.id)} />
          {body.games.length === 0 ? (
            <p className="empty">No games are on the live schedule right now.</p>
          ) : (
            byEtDay(body.games).map(([day, games]) => (
              <section className="fixtures" key={day} aria-label={day}>
                <p className="eyebrow">{day}</p>
                <ul>
                  {games.map((g) => (
                    <li key={g.id}>
                      {/* No prefetch: opening a game lists its bets, which costs a credit. */}
                      <Link className="fixture" href={`/live/${g.id}`} prefetch={false}>
                        <strong>{g.label}</strong>
                        <span className="fixture-meta">
                          {g.in_play ? "In play · " : ""}<LocalTime iso={g.commence_time} />
                        </span>
                        <span className="chev" aria-hidden="true">›</span>
                      </Link>
                    </li>
                  ))}
                </ul>
              </section>
            ))
          )}
          <p className="page-note">
            Opening a game costs 1 credit to list its bets (kept for 5 minutes). Each new question
            costs 1 credit or more, and a repeat within a minute is free.
          </p>
        </>
      )}
      <p>
        <Link className="pill" href="/archive">
          The archive: what a $10 bet 45 minutes before kickoff would have paid
          <span aria-hidden="true">›</span>
        </Link>
      </p>
    </>
  );
}
