"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { dayAndTime } from "@/lib/format";
import { playHref, resultHref } from "@/lib/links";
import type { LiveGame, LiveResponse } from "@/lib/types";

const IN_PLAY = new Set(["in_progress", "break", "delayed"]);

function GameBlock({ g }: { g: LiveGame }) {
  const inPlay = IN_PLAY.has(g.status);
  const score = g.home.score !== null && g.away.score !== null
    ? `${g.away.abbr ?? g.away.name} ${g.away.score} – ${g.home.score} ${g.home.abbr ?? g.home.name}`
    : g.label;
  return (
    <section className="game" aria-label={g.label}>
      <div className="head">
        <span className="score">{score}</span>
        <span className="status">
          {g.status === "scheduled" ? dayAndTime(g.commence_time) : g.status_text}
        </span>
      </div>
      {g.plays.length > 0 && (
        <div className="chips">
          {g.plays.map((p) =>
            p.athlete_id ? (
              <Link key={p.id} href={playHref(g.id, p.athlete_id, p.kind, p.yards)}
                    className={`chip${p.scoring ? " scoring" : ""}`}>
                {p.label}
                {p.clock && p.period ? <span className="muted small">&nbsp;· Q{p.period} {p.clock}</span> : null}
              </Link>
            ) : null,
          )}
        </div>
      )}
      <div className="team-links">
        {[g.away, g.home].map((t) => t.espn_id && (
          <Link key={t.espn_id} className="chip" href={resultHref("team", g.id, t.espn_id)}>
            {t.abbr ?? t.name} lines
          </Link>
        ))}
        {!inPlay && g.plays.length === 0 && g.status === "scheduled" && (
          <span className="hint">Prices are archived 45 minutes before kickoff.</span>
        )}
      </div>
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
  const other = games.filter((g) => !IN_PLAY.has(g.status));
  return (
    <div aria-live="polite">
      <h2>{live.length ? "Live now" : "No games in play right now"}</h2>
      {failed && <p className="hint" role="alert">Can&apos;t load games right now. Retrying.</p>}
      {live.map((g) => <GameBlock key={g.id} g={g} />)}
      {!live.length && data?.next_kickoff && (
        <p className="muted">Next kickoff: {data.next_kickoff.label}, {dayAndTime(data.next_kickoff.commence_time)}</p>
      )}
      {other.length > 0 && <h2>{live.length ? "Other games" : "Recent and upcoming"}</h2>}
      {other.map((g) => <GameBlock key={g.id} g={g} />)}
    </div>
  );
}
