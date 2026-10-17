import Link from "next/link";
import LocalTime from "@/components/LocalTime";
import { etDay, scoreline } from "@/lib/format";
import { recapHref } from "@/lib/links";
import { currentOwner, meGet } from "@/lib/me";
import type { RecapGame, RecapState } from "@/lib/types";

export const dynamic = "force-dynamic";

const STATE: Record<RecapState, string> = {
  pregame: "Not started", in_play: "In play", final: "Final", checked: "Final, checked",
  off: "Called off",
};

/** Every game the owner signed in looked something up in, latest first, each opening its
 *  recap. */
export default async function MyLookups() {
  const owner = await currentOwner();
  const data = owner ? await meGet<{ games: RecapGame[] }>("recaps", owner) : null;
  const games = data?.games ?? [];
  return (
    <>
      <h1>Your lookups</h1>
      <p className="muted">
        Every bet page you open is kept here, for you only. After a game, its recap shows how
        each bet went, to look back on before the next one.
      </p>
      {games.length === 0 ? (
        <p className="muted">
          Nothing yet. Open a player&apos;s bets, or tap a play on a game&apos;s page, and it
          shows up here.
        </p>
      ) : (
        <div className="tiles">
          {games.map(({ game, state, lookups }) => (
            <Link key={game.id} className="tile small" href={recapHref(game.id)}
                  prefetch={false}>
              <strong>{scoreline(game)}</strong>
              <span className="muted">
                {game.status === "scheduled"
                  ? <LocalTime iso={game.commence_time} withDay />
                  : <>{etDay(game.commence_time)} · {STATE[state]}</>}
                {" · "}{lookups} {lookups === 1 ? "lookup" : "lookups"}
              </span>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}
