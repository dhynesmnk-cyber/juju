"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import CreditsMeter from "@/components/CreditsMeter";
import LiveAnswerView from "@/components/LiveAnswerView";
import LocalTime from "@/components/LocalTime";
import MarketsPanel from "@/components/MarketsPanel";
import {
  type ChatGame, type Credits, type LiveAnswer, type PlanCost, type Turn, askErrorText,
  loadThread, newTurnId, normalizeAnswer, saveThread,
} from "@/lib/liveChat";

const IDLE_COST = "Each new question costs 1 credit or more. Repeats within a minute are free.";

function store(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;  // blocked (private window, site data off): the chat still works, unsaved
  }
}

/** The live chat for one game: ask about a player or team in words, get the price a book posts
 *  now. Fetches /api/live/ask on each question; shows what it will cost beforehand; keeps the
 *  box focused for the next one; keeps the thread on this device (docs/plans/live-chat.md). */
export default function LiveChat({ game, initialCredits }: {
  game: ChatGame;
  initialCredits: Credits;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [text, setText] = useState("");
  const [plan, setPlan] = useState<PlanCost | null>(null);
  const [busy, setBusy] = useState(false);
  const [credits, setCredits] = useState<Credits | null>(initialCredits);
  const [now, setNow] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const end = useRef<HTMLDivElement>(null);

  // The saved thread comes back once the page is on the device (never in the server's HTML). A
  // question that was still being answered when the page closed is said to have been cut off.
  useEffect(() => {
    const t = setTimeout(() => {
      setTurns(loadThread(store(), game.id).map((x) => x.answer || x.error ? x
        : { ...x, error: "That question was cut off. Ask it again." }));
      setLoaded(true);
      setNow(Date.now());
    }, 0);
    return () => clearTimeout(t);
  }, [game.id]);

  useEffect(() => {
    if (loaded) saveThread(store(), game.id, turns);
  }, [turns, loaded, game.id]);

  // Old prices are marked as old, so the clock ticks.
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 10_000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    end.current?.scrollIntoView({ block: "nearest" });
  }, [turns.length]);

  // What the question will cost, before it is asked: the plan call reads the words and the
  // cache, and never reaches The Odds API.
  useEffect(() => {
    const q = text.trim();
    if (q.length < 3) {
      const t = setTimeout(() => setPlan(null), 0);
      return () => clearTimeout(t);
    }
    const ctl = new AbortController();
    const t = setTimeout(async () => {
      try {
        const r = await fetch(
          `/api/live/plan?game_id=${encodeURIComponent(game.id)}&q=${encodeURIComponent(q)}`,
          { signal: ctl.signal });
        if (r.ok) setPlan((await r.json()).cost ?? null);
      } catch {
        if (!ctl.signal.aborted) setPlan(null);
      }
    }, 350);
    return () => { clearTimeout(t); ctl.abort(); };
  }, [text, game.id]);

  const ask = useCallback(async (raw: string) => {
    const question = raw.trim();
    if (!question || busy) return;
    const at = Date.now();
    const turn: Turn = { id: newTurnId(at), at, text: question, answer: null, error: null };
    setTurns((t) => [...t, turn]);
    setText("");
    setPlan(null);
    setBusy(true);
    let patch: Pick<Turn, "answer" | "error">;
    try {
      const r = await fetch("/api/live/ask", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ game_id: game.id, text: question }),
      });
      if (r.ok) {
        const answer: LiveAnswer = normalizeAnswer(await r.json());
        setCredits(answer.credits);
        patch = { answer, error: null };
      } else {
        patch = { answer: null, error: askErrorText(r.status) };
      }
    } catch {
      patch = { answer: null, error: askErrorText(null) };
    }
    setTurns((t) => t.map((x) => (x.id === turn.id ? { ...x, ...patch } : x)));
    setNow(Date.now());
    setBusy(false);
    input.current?.focus();  // ready for the next one
  }, [busy, game.id]);

  return (
    <section className="live" aria-label="Live odds">
      <p><Link className="pill" href="/"><span aria-hidden="true">‹</span> All games</Link></p>
      <header className="live-head">
        <p className="eyebrow">Live odds{game.in_play ? " · In play" : ""}</p>
        <h1>{game.label}</h1>
        <p className="sub"><LocalTime iso={game.commence_time} withDay /></p>
      </header>
      <CreditsMeter credits={credits} />
      <MarketsPanel gameId={game.id} onCredits={setCredits} />

      <div role="log" aria-label="Your questions and the live prices">
        <ol className="thread">
          {turns.map((t) => (
            <LiveAnswerView key={t.id} turn={t} now={now} onAskAgain={(q) => void ask(q)} />
          ))}
        </ol>
      </div>
      <div ref={end} />

      <form className="live-form" onSubmit={(e) => { e.preventDefault(); void ask(text); }}
            role="search">
        <label htmlFor="live-q">Ask about a player or team</label>
        <div className="row">
          <input id="live-q" ref={input} value={text} autoFocus autoComplete="off"
                 autoCapitalize="words" enterKeyHint="send" maxLength={200}
                 placeholder="e.g. Fannin got 4 catches"
                 onChange={(e) => setText(e.target.value)} />
          <button type="submit" className="btn" disabled={busy || !text.trim()}>
            {busy ? "…" : "Ask"}<span className="sr-only">: get the live price</span>
          </button>
        </div>
        <p className="cost" aria-live="polite">{plan ? plan.text : IDLE_COST}</p>
      </form>
      <p className="page-note">
        Live prices, as of the time shown. Juju is not a sportsbook and does not take bets.
      </p>
    </section>
  );
}
