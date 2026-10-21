"use client";

import Link from "next/link";
import { type KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import LocalTime from "@/components/LocalTime";
import MiniCard from "@/components/MiniCard";
import Jujus from "@/components/world/Jujus";
import WorldStage from "@/components/world/WorldStage";
import { quarter, secondsAgo } from "@/lib/format";
import { resultHref } from "@/lib/links";
import { remember as rememberLookup } from "@/lib/remember";
import type { GamePageView, GamePlay, PlayerView, TeamView } from "@/lib/types";

const IN_PLAY = new Set(["in_progress", "break", "delayed"]);
type Tab = "plays" | "players" | "lines";
const TABS: [Tab, string][] = [["plays", "Plays"], ["players", "Players"], ["lines", "Game lines"]];
type Answer = { playId: number; view: PlayerView | TeamView | null; failed: boolean };

const BADGE: Record<string, [string, string]> = {
  touchdown: ["TD", "td"], field_goal: ["FG", "fg"], def_interception: ["INT", "int"],
  run: ["RUN", "plain"], catch: ["CATCH", "plain"],
};

/** Whose play it is, as a page: the player's, or the team's for a score with no one named. */
function target(p: GamePlay): { kind: "player" | "team"; id: string } | null {
  if (p.athlete_id) return { kind: "player", id: p.athlete_id };
  if (p.team_espn_id) return { kind: "team", id: p.team_espn_id };
  return null;
}

/** The plays (newest first) in runs of one quarter each. */
function byQuarter(plays: GamePlay[]): [number | null, GamePlay[]][] {
  const out: [number | null, GamePlay[]][] = [];
  for (const p of plays) {
    const last = out[out.length - 1];
    if (last && last[0] === p.period) last[1].push(p);
    else out.push([p.period, [p]]);
  }
  return out;
}

function quarterName(period: number | null): string {
  if (!period) return "Plays";
  if (period <= 4) return ["1st", "2nd", "3rd", "4th"][period - 1] + " quarter";
  return period === 5 ? "Overtime" : `${period - 4}OT`;
}

/** What a play paid: the priced bets it touched (two at most), and the way to the rest. */
function MiniAnswer({ gameId, play, answer }: { gameId: number; play: GamePlay; answer: Answer | null }) {
  const t = target(play);
  if (!t) return null;
  const href = resultHref(t.kind, gameId, t.id, undefined, play.id);
  if (!answer || answer.playId !== play.id) {
    return <p className="muted" role="status">Getting what it paid…</p>;
  }
  if (answer.failed || !answer.view) {
    return <p className="muted">Couldn&apos;t load its bets just now. <Link href={href}>Open them</Link></p>;
  }
  const v = answer.view;
  const priced = v.cards.filter((c) => c.price !== null);
  const touched = priced.filter((c) => c.touched);
  const show = (touched.length ? touched : priced).slice(0, 2);
  const who = "player" in v ? v.player.name : v.team.name;
  const cashed = show[0] && (show[0].outcome === "locked" || show[0].outcome === "won");
  return (
    <div className={`mini${cashed ? " crewed" : ""}`}>
      {cashed && <Jujus mood="joy" />}
      {show.length === 0 && (
        <p className="muted">
          {("no_price" in v && v.no_price) || "No price on file for the bets this play touched."}
        </p>
      )}
      {show.map((c) => <MiniCard key={c.key} c={c} />)}
      <Link className="mini-all" href={href}>All of {who}&apos;s bets <span aria-hidden="true">›</span></Link>
    </div>
  );
}

/** One game (docs/plans/live-test-fixes.md, direction B): its plays on a timeline, newest
 *  first, each opening what it paid in place; its players; its game lines. `remember`: a
 *  signed-in owner, whose opened plays are saved for their recap. */
export default function GamePage({ initial, remember = false }: {
  initial: GamePageView; remember?: boolean;
}) {
  const [g, setG] = useState(initial);
  const [tab, setTab] = useState<Tab>("plays");
  const [open, setOpen] = useState<number | null>(null);
  const [answer, setAnswer] = useState<Answer | null>(null);
  const wanted = useRef<number | null>(null);
  const live = IN_PLAY.has(g.status);

  useEffect(() => {
    if (!live) return;
    let alive = true;
    const t = setInterval(async () => {
      try {
        const r = await fetch(`/api/game/${g.id}`);
        if (r.ok && alive) setG(await r.json());
      } catch {
        /* the next poll tries again */
      }
    }, 10_000);
    return () => { alive = false; clearInterval(t); };
  }, [g.id, live]);

  const toggle = useCallback(async (p: GamePlay) => {
    if (wanted.current === p.id) {
      wanted.current = null;
      setOpen(null);
      return;
    }
    wanted.current = p.id;
    setOpen(p.id);
    const t = target(p);
    if (!t) return;
    let next: Answer;
    try {
      const r = await fetch(`/api/${t.kind}/${g.id}/${t.id}?play_id=${p.id}`);
      if (!r.ok) throw new Error();
      next = { playId: p.id, view: await r.json(), failed: false };
      if (remember) rememberLookup({ kind: t.kind, game_id: g.id, target: t.id, play_id: p.id });
    } catch {
      next = { playId: p.id, view: null, failed: true };
    }
    if (wanted.current === p.id) setAnswer(next);  // a later tap wins over a slow answer
  }, [g.id, remember]);

  const onTabKey = (e: KeyboardEvent<HTMLButtonElement>) => {
    const i = TABS.findIndex(([t]) => t === tab);
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const [t] = TABS[(i + step + TABS.length) % TABS.length];
    setTab(t);
    document.getElementById(`tab-${t}`)?.focus();
  };

  const openPlay = g.plays.find((p) => p.id === open) ?? null;
  const top = openPlay && answer?.playId === openPlay.id
    ? answer.view?.cards.find((c) => c.price !== null && c.touched) : undefined;
  const world = top && (top.outcome === "locked" || top.outcome === "won") ? "cash"
    : live ? "live" : g.status === "scheduled" ? "pre" : "calm";
  const sides = [g.away, g.home];
  const names = new Map(g.players.map((p) => [p.id, p.name]));

  return (
    <>
      <WorldStage world={world} />
      <div className="game-head">
        <Link className="back" href="/">‹ Games</Link>
        <h1 className="scoreline">
          {g.away.abbr ?? g.away.name} {g.away.score ?? ""} – {g.home.score ?? ""} {g.home.abbr ?? g.home.name}
        </h1>
        <p className="sub">
          {g.status === "scheduled" ? <>Kickoff <LocalTime iso={g.commence_time} withDay /></> : g.status_text}
          {live && <span className={`fresh ${g.freshness}`}> · live stats {secondsAgo(g.updated_seconds_ago)}</span>}
        </p>
      </div>

      <div role="tablist" aria-label="This game" className="tabs">
        {TABS.map(([t, label]) => (
          <button key={t} type="button" role="tab" id={`tab-${t}`} aria-selected={tab === t}
                  aria-controls={`panel-${t}`} tabIndex={tab === t ? 0 : -1}
                  onClick={() => setTab(t)} onKeyDown={onTabKey}>{label}</button>
        ))}
      </div>

      <div role="tabpanel" id="panel-plays" aria-labelledby="tab-plays" hidden={tab !== "plays"}
           className="game-layout">
        <div className="timeline">
          {g.plays.length === 0 && (
            <p className="muted">
              {g.status === "scheduled"
                ? "No plays yet. Prices are saved 45 minutes before kickoff."
                : "No notable plays yet: scores, 20+ yard gains and interceptions show up here."}
            </p>
          )}
          {byQuarter(g.plays).map(([period, plays]) => (
            <section key={String(period)} className="quarter" aria-label={quarterName(period)}>
              <h2>{quarterName(period)}</h2>
              <ul>
                {plays.map((p) => (
                  <li key={p.id}>
                    <button type="button" className={`play-row${open === p.id ? " open" : ""}`}
                            aria-expanded={open === p.id} onClick={() => toggle(p)}>
                      <span className="what">
                        <span>
                          {BADGE[p.kind] && <span className={`badge ${BADGE[p.kind][1]}`}>{BADGE[p.kind][0]}</span>}
                          {p.label}
                        </span>
                        {p.passer_id && names.has(p.passer_id) && (
                          <span className="by">
                            {p.kind === "def_interception" ? "thrown by " : "from "}
                            {names.get(p.passer_id)}
                          </span>
                        )}
                      </span>
                      <span className="when">{p.clock}</span>
                    </button>
                    {open === p.id && (
                      <div className="inline-answer">
                        <MiniAnswer gameId={g.id} play={p} answer={answer} />
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
        <aside className="answer-panel" aria-label="What it paid">
          {openPlay ? (
            <>
              <p className="eyebrow">The play</p>
              <h2>{openPlay.label}</h2>
              <p className="muted">{quarter(openPlay.period)} {openPlay.clock}</p>
              <MiniAnswer gameId={g.id} play={openPlay} answer={answer} />
            </>
          ) : (
            <p className="muted">Tap a play to see what $10 at the price 45 minutes before kickoff paid.</p>
          )}
        </aside>
      </div>

      <div role="tabpanel" id="panel-players" aria-labelledby="tab-players" hidden={tab !== "players"}>
        {g.players.length === 0 && <p className="muted">No player has a price on file for this game.</p>}
        {sides.map((side) => {
          const players = g.players.filter((p) => p.team_espn_id === side.espn_id);
          if (!players.length) return null;
          return (
            <section key={side.espn_id ?? side.name}>
              <h2>{side.name}</h2>
              <ul className="player-list">
                {players.map((p) => (
                  <li key={p.id}>
                    <Link href={resultHref("player", g.id, p.id)}>
                      {p.name}{p.position ? <span className="muted small">{p.position}</span> : null}
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          );
        })}
      </div>

      <div role="tabpanel" id="panel-lines" aria-labelledby="tab-lines" hidden={tab !== "lines"}>
        <p className="muted">Moneyline, spread and totals, at the price 45 minutes before kickoff.</p>
        <div className="team-links">
          {sides.map((s) => s.espn_id && (
            <Link key={s.espn_id} className="chip" href={resultHref("team", g.id, s.espn_id)}>
              {s.abbr ?? s.name} lines
            </Link>
          ))}
        </div>
      </div>
    </>
  );
}
