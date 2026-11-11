"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import LocalTime from "@/components/LocalTime";
import Jujus from "@/components/world/Jujus";
import WorldStage from "@/components/world/WorldStage";
import { byEtDay, quarter } from "@/lib/format";
import { gameHref, playHref } from "@/lib/links";
import type { LiveGame, LiveResponse, Side } from "@/lib/types";

const IN_PLAY = new Set(["in_progress", "break", "delayed"]);

/** "Philadelphia Eagles" -> "Eagles": the scoreboard's second word. */
function nickname(s: Side): string {
  const words = s.name.split(" ");
  return words[words.length - 1];
}

function TeamRows({ g }: { g: LiveGame }) {
  const away = g.away.score ?? 0, home = g.home.score ?? 0;
  const rows: [Side, boolean][] = [[g.away, away >= home], [g.home, home >= away]];
  return (
    <span className="team-rows">
      {rows.map(([s, ahead]) => (
        <span key={s.espn_id ?? s.name} className={`team-row${ahead ? "" : " behind"}`}>
          <span className="abbr">{s.abbr ?? s.name}</span>{" "}
          <span className="nick">{nickname(s)}</span>{" "}
          <span className="pts num">{s.score ?? ""}</span>
        </span>
      ))}
    </span>
  );
}

/** A game on now, as a match card: the score strip, its latest play (straight to what it
 *  paid) and the way into every play. */
function MatchCard({ g, crew }: { g: LiveGame; crew: boolean }) {
  const latest = g.plays.find((p) => p.athlete_id);
  return (
    <section className="match" aria-label={g.label}>
      {crew && <Jujus mood="cheer" />}
      <p className="match-strip">
        <span className="live-tag"><span className="dot" aria-hidden="true" /> LIVE</span>
        <span className="match-clock">{g.status_text}</span>
      </p>
      <Link className="match-score" href={gameHref(g.id)} aria-label={`${g.label}, every play`}>
        <TeamRows g={g} />
      </Link>
      {latest && latest.athlete_id && (
        <Link className="match-latest"
              href={playHref(g.id, latest.athlete_id, latest.kind, latest.yards, latest.id)}>
          <span className="eyebrow">Latest · {quarter(latest.period)} {latest.clock}</span>
          <span className="match-play">{latest.label}</span>
          <span className="chev" aria-hidden="true">›</span>
        </Link>
      )}
      <Link className="btn match-cta" href={gameHref(g.id)}>
        Every play, and what it paid <span aria-hidden="true">›</span>
      </Link>
    </section>
  );
}

export default function LiveNow({ initial }: { initial: LiveResponse | null }) {
  const [data, setData] = useState<LiveResponse | null>(initial);
  const [failed, setFailed] = useState(initial === null);
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const r = await fetch("/api/live");
        if (!r.ok) throw new Error();
        const body: LiveResponse = await r.json();
        if (alive) { setData(body); setFailed(false); }
      } catch {
        if (alive) setFailed(true);
      }
    };
    const t = setInterval(load, 10_000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  const games = data?.games ?? [];
  const live = games.filter((g) => IN_PLAY.has(g.status));
  const next = games.filter((g) => g.status === "scheduled");
  const earlier = games.filter((g) => !IN_PLAY.has(g.status) && g.status !== "scheduled");
  // The home page never celebrates: it is calm before kickoff and lit when games are on.
  const world = live.length ? "live" : "pre";
  return (
    <div>
      <WorldStage world={world} dense />
      {!live.length && <div className="home-crew"><Jujus mood="sleepy" /></div>}
      <h2>{live.length ? "On now" : "No games in play right now"}</h2>
      {failed && <p className="page-note" role="alert">Can&apos;t load games right now. Retrying.</p>}
      {!live.length && !next.length && data?.next_kickoff && (
        <p className="page-note">
          Next kickoff: {data.next_kickoff.label}, <LocalTime iso={data.next_kickoff.commence_time} withDay />
        </p>
      )}
      <div className={`matches${live.length ? " crewed" : ""}`}>
        {live.map((g, i) => <MatchCard key={g.id} g={g} crew={i === 0} />)}
      </div>
      {next.length > 0 && (
        <>
          <h2>Next</h2>
          <p className="page-note">Prices are saved 45 minutes before kickoff.</p>
          {byEtDay(next).map(([day, games]) => (
            <section key={day} className="fixtures" aria-label={day}>
              <h3 className="eyebrow">{day}</h3>
              <ul>
                {games.map((g) => (
                  <li key={g.id}>
                    <Link className="fixture" href={gameHref(g.id)}>
                      <strong>{g.label}</strong>
                      <span className="fixture-meta"><LocalTime iso={g.commence_time} /></span>
                      <span className="chev" aria-hidden="true">›</span>
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </>
      )}
      {earlier.length > 0 && (
        <>
          <h2>Earlier this week</h2>
          <section className="fixtures" aria-label="Earlier this week">
            <ul>
              {earlier.map((g) => (
                <li key={g.id}>
                  <Link className="fixture" href={gameHref(g.id)}>
                    <strong className="num">
                      {g.away.abbr ?? g.away.name} {g.away.score ?? ""} – {g.home.score ?? ""} {g.home.abbr ?? g.home.name}
                    </strong>
                    <span className="fixture-meta">{g.status_text} · every play, and what it paid</span>
                    <span className="chev" aria-hidden="true">›</span>
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}
    </div>
  );
}
