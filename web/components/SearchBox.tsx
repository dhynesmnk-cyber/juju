"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import { choiceHref, resultHref } from "@/lib/links";
import type { Choice, Focus, LookupResponse } from "@/lib/types";

/** Typeahead for players in live games first, and free text for anything else. The
 *  confirmation step appears only when the lookup isn't sure (docs/GOALS.md section 7.3). */
export default function SearchBox() {
  const router = useRouter();
  const [text, setText] = useState("");
  const [suggestions, setSuggestions] = useState<Choice[]>([]);
  const [active, setActive] = useState(-1);
  const [choices, setChoices] = useState<Choice[]>([]);
  const [focus, setFocus] = useState<Partial<Focus> | undefined>();
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const listId = useId();
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => {
    clearTimeout(timer.current);
    const q = text.trim();
    // A single word is a name being typed; several words are a description of a play.
    if (q.length < 2 || q.split(/\s+/).length > 2) {
      timer.current = setTimeout(() => setSuggestions([]), 0);
      return;
    }
    timer.current = setTimeout(async () => {
      try {
        const r = await fetch(`/api/suggest?q=${encodeURIComponent(q)}`);
        if (r.ok) setSuggestions((await r.json()).choices ?? []);
      } catch {
        setSuggestions([]);
      }
    }, 120);
    return () => clearTimeout(timer.current);
  }, [text]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (active >= 0 && suggestions[active]) {
      router.push(choiceHref(suggestions[active]));
      return;
    }
    const q = text.trim();
    if (!q) return;
    setBusy(true);
    setMessage(null);
    setChoices([]);
    try {
      const r = await fetch("/api/lookup", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ text: q }),
      });
      if (r.status === 429) {
        setMessage("That's a lot of lookups. Give it a minute.");
        return;
      }
      if (!r.ok) {
        setMessage("Juju couldn't look that up right now. Try again, or pick from Live now.");
        return;
      }
      const body: LookupResponse = await r.json();
      if ((body.kind === "player" || body.kind === "team") && body.game_id && body.id) {
        router.push(resultHref(body.kind, body.game_id, body.id, body.focus));
      } else if (body.kind === "choices") {
        setChoices(body.choices);
        setFocus(body.focus);
      } else {
        setMessage(body.message);
      }
    } catch {
      setMessage("Juju couldn't look that up right now. Try again, or pick from Live now.");
    } finally {
      setBusy(false);
    }
  }

  function onKey(e: React.KeyboardEvent) {
    if (!suggestions.length) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(a + 1, suggestions.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, -1));
    } else if (e.key === "Escape") {
      setSuggestions([]);
      setActive(-1);
    }
  }

  return (
    <form className="search" onSubmit={submit} role="search">
      <label htmlFor="q">What just happened?</label>
      <div className="row">
        <input
          id="q" name="q" value={text} autoComplete="off" autoCapitalize="words"
          enterKeyHint="search" placeholder="e.g. Barkley 60 yd TD run" maxLength={200}
          onChange={(e) => { setText(e.target.value); setActive(-1); }}
          onKeyDown={onKey}
          role="combobox" aria-expanded={suggestions.length > 0} aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={active >= 0 ? `${listId}-${active}` : undefined}
        />
        <button type="submit" disabled={busy} aria-label="Look it up">
          {busy ? "…" : "Go"}
        </button>
      </div>
      {suggestions.length > 0 && (
        <ul className="suggestions" id={listId} role="listbox" aria-label="Players">
          {suggestions.map((c, i) => (
            <li key={`${c.game_id}-${c.id}`} id={`${listId}-${i}`} role="option"
                aria-selected={i === active}>
              <Link href={choiceHref(c)}>
                <span>{c.name}</span><span className="muted small">{c.detail}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
      {choices.length > 0 && (
        <div className="choices" aria-live="polite">
          <p className="hint">Which one did you mean?</p>
          <ul className="suggestions" aria-label="Did you mean">
            {choices.map((c) => (
              <li key={`${c.kind}-${c.game_id}-${c.id}`}>
                <Link href={choiceHref(c, focus)}>
                  <span>{c.name}</span><span className="muted small">{c.detail}</span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}
      {message && <p className="hint" role="alert">{message}</p>}
    </form>
  );
}
