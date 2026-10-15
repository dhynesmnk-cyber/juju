"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import LocalTime from "@/components/LocalTime";
import Jujus from "@/components/world/Jujus";
import WorldStage from "@/components/world/WorldStage";
import { quarter } from "@/lib/format";
import { gameHref, playHref } from "@/lib/links";
import type { LiveGame, LiveResponse, Side } from "@/lib/types";

const IN_PLAY = new Set(["in_progress", "break", "delayed"]);

/** "Philadelphia Eagles" -> "Eagles": the scoreboard's second line. */
function nickname(s: Side): string {
  const words = s.name.split(" ");
  return words[words.length - 1];
}

function ScoreRows({ g }: { g: LiveGame }) {
  const away = g.away.score ?? 0, home = g.home.score ?? 0;
  const rows: [Side, number | null, boolean][] = [
    [g.away, g.away.score, away >= home], [g.home, g.home.score, home >= away],
  ];
  return (
    <span className="score-rows">
      {rows.map(([s, score, ahead]) => (
        <span key={s.espn_id ?? s.name} className={`score-row${ahead ? "" : " behind"}`}>
          <span>{s.abbr ?? s.name} <span className="nick">{nickname(s)}</span></span>
          <span className="pts">{score ?? ""}</span>
        </span>
      ))}
    </span>
  );
}

/** A game on now: the score, its latest play (straight to what it paid) and the way into
 *  every play. */
function LiveTile({ g, crew }: { g: LiveGame; crew: boolean }) {
  const latest = g.plays.find((p) => p.athlete_id);
  return (
    <section className="tile live" aria-label={g.label}>
      {crew && <Jujus mood="cheer" />}
      <p className="tile-status"><span className="dot" aria-hidden="true" /> LIVE · {g.status_text}</p>
      <Link className="tile-score" href={gameHref(g.id)} aria-label={`${g.label}, every play`}>
        <ScoreRows g={g} />
      </Link>
      {latest && latest.athlete_id && (
        <p className="tile-latest">
          <span className="eyebrow">Latest</span>
          <Link href={playHref(g.id, latest.athlete_id, latest.kind, latest.yards, latest.id)}>
            {latest.label}
          </Link>
          <span className="muted"> · {quarter(latest.period)} {latest.clock}</span>
        </p>
      )}
      <Link className="tile-cta" href={gameHref(g.id)}>
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
      <WorldStage world={world} />
      {!live.length && <div className="home-crew"><Jujus mood="sleepy" /></div>}
      <h2>{live.length ? "On now" : "No games in play right now"}</h2>
      {failed && <p className="hint" role="alert">Can&apos;t load games right now. Retrying.</p>}
      {!live.length && data?.next_kickoff && (
        <p className="muted">
          Next kickoff: {data.next_kickoff.label}, <LocalTime iso={data.next_kickoff.commence_time} withDay />
        </p>
      )}
      <div className={`tiles${live.length ? " crewed" : ""}`}>
        {live.map((g, i) => <LiveTile key={g.id} g={g} crew={i === 0} />)}
      </div>
      {next.length > 0 && (
        <>
          <h2>Next</h2>
          <div className="tiles">
            {next.map((g) => (
              <Link key={g.id} className="tile small" href={gameHref(g.id)}>
                <strong>{g.label}</strong>
                <span className="muted"><LocalTime iso={g.commence_time} withDay /></span>
                <span className="muted small">Prices are saved 45 minutes before kickoff</span>
              </Link>
            ))}
          </div>
        </>
      )}
      {earlier.length > 0 && (
        <>
          <h2>Earlier</h2>
          <div className="tiles">
            {earlier.map((g) => (
              <Link key={g.id} className="tile small" href={gameHref(g.id)}>
                <strong>
                  {g.away.abbr ?? g.away.name} {g.away.score ?? ""} – {g.home.score ?? ""} {g.home.abbr ?? g.home.name}
                </strong>
                <span className="muted">{g.status_text} · every play, and what it paid ›</span>
              </Link>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
