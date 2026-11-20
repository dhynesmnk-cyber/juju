# The live chat: live odds on demand

**Date:** 2026-11-20. **Decision:** [GOALS decision 14](../GOALS.md). **Licensing:** the third
exception ([licensing.md](../licensing.md)). **Status:** steps 1 (the backend), 2 (the site) and 3 (the
assistant) are built; step 4 and what the owner must do are below. Update this table as each step lands.

| Step | What | Status |
|---|---|---|
| 1 | The backend: parser, rules, cache and credits, the endpoints, their tests, the docs | **done** (2026-11-20) |
| 2 | The site: game picker, `LiveChat`, markets panel, credit meter; the home page swap; the archive at `/archive` | **done** (2026-11-20) |
| 3 | The assistant: follow-up questions with DeepSeek Flash, every number from the server | **built** (2026-11-20), not yet tried against the real DeepSeek API |
| 4 | Hardening | the privacy page, the deploy notes and `HANDOVER.md` are done with step 3; what is left is trying it for real (below) |

## What the owner asked for

The product gains a live mode: pick a game from the live schedule, then ask for any market for one
player or team in it and see the live price in a chat. The T-45 archive is unchanged.

The twelve answers that shaped it (2026-11-20):

1. **The answer** is a live price now, in the present tense ("pays +180 … $10 would return
   $28.00 if it hits"), plus where the player stands in the game ("he has 3 so far"), from the
   box score Juju already follows. Live odds cannot say what a bet placed earlier would have
   paid, and archived prices are banned from this chat.
2. **A missing line.** "4 catches" is Over 3.5. If the main market doesn't post that line, fetch
   the alternate market (one more credit), then say the nearest lines if it still isn't there.
3. **Books** follow the cards: the book of record (`BOOKS`, Hard Rock Bet first) leads, the
   other books follow, the best price is never the headline, offshore books are never shown.
4. **Stake** is always $10, worked out by `core/odds.py` on the server.
5. **One player or team per answer.** A question that names none gets "Which player?" and names,
   never prices. No market boards.
6. **Who:** everyone past the site's passcode gate, with no per-visitor cap. So the live chat is
   off at the site (404) unless `SITE_PASSCODE` is set.
7. **Credits:** live questions are never blocked. A warning shows at `LIVE_LOW_CREDITS` (2,000).
   The archive's own rationing (`core/budget.py`) absorbs the shortfall: on a busy night the T-45
   captures may go without prices. The owner accepted this.
8. **The thread** is kept in the browser, per game, until the game ends; each answer is stamped
   "as of" and greyed when stale. Nothing is stored on the server.
9. **Scope:** player props, team lines and live parlays (2 to 6 legs).
10. **Placement:** the live chat becomes the home page. The archive moves, unchanged, to
    `/archive`; its pages (`/g/…`) and share images don't move. The game list is the free
    `/events` call, not the paid `/odds` one the brief named.
11. **The assistant** uses DeepSeek Flash through DeepSeek's own API (OpenAI-format,
    `https://api.deepseek.com`), with about 300 assistant messages a day (a setting) before
    answers fall back to templates.
12. **Its scope** is this game's odds and stats only. It explains the numbers it shows. Anything
    else, and "should I bet it?", gets a fixed polite refusal: facts, never picks.

## Step 1: the backend (built)

- `ingest/parse.py` `read_live`: over or under, the line the words name ("4 catches" → Over 3.5,
  "5+" → Over 4.5, "over 45.5" as written), a team's handicap. `parse_text` is unchanged.
- `core/livechat.py`: the rules, pure. `answer_for` picks the exact line or the nearest posted
  line each side; the book chain; `price_parlay`; `cost_of`; the wording. (`core/live.py` is the
  live scores, a different thing.)
- `ingest/live_odds.py`: the one place a question reaches The Odds API. A cache of at most 60
  seconds (a setting can shorten it, never lengthen it), keyed by game and market so a second
  player is free; the market list for 5 minutes; a lock per key, so two askers make one call;
  the credits left are The Odds API's own `x-requests-remaining`. It has its own in-memory
  breaker and writes only the credit count to `source_health`, never the worker's breaker state.
- `ingest/odds_api.py`: `event_markets` (`/events/{id}/markets`).
- `api/live_chat.py`:
  - `GET /api/live/games`: the schedule. Free. No prices.
  - `GET /api/live/markets?game_id=`: the markets a game offers. Names only. 1 credit, cached
    5 minutes.
  - `GET /api/live-markets?game_id&market&player`: one player's or team's lines in one market.
    `player` is required (400 without it). 404 "Market not available…" when it isn't posted.
  - `GET /api/live/plan?game_id&q`: what a question will cost, before it is asked. Never calls
    out.
  - `POST /api/live/ask {game_id, text}`: free text in, one plain-language answer out, with the
    legs, the credits, the cost of this question and when the prices are from.
- Settings: `LIVE_CACHE_SECONDS` (60), `LIVE_MARKETS_CACHE_SECONDS` (300), `LIVE_LOW_CREDITS`
  (2000). The live chat needs `ODDS_API_KEY`; without it the routes answer 503.
- Tests: `tests/unit/test_parse_live.py`, `test_livechat.py`, `test_live_odds.py`,
  `tests/db/test_live_chat.py`, all on the recorded PIT @ CLE reply (no new recording).

## Step 2: the site (built)

- **The front page depends on the passcode.** With `SITE_PASSCODE` set (the laptop preview), `/`
  is the live home (`components/LiveHome.tsx`: the schedule, grouped by Eastern day, the credits
  left, no prices) and the T-45 archive is at `/archive` (`components/ArchiveHome.tsx`, the old
  front page unchanged). Without one, the live chat is off (decision 6 above: no per-visitor cap,
  so a passcode must stand in front), `/` is the archive exactly as before, the header shows no
  "Archive" link, and `/live/…` and the live API are 404. So CI, a public launch and every
  existing test and visual snapshot see the site as it was. The archive's own "back" links go to
  `/archive`; the header gains "Archive" and the footer says what is live and what is
  hypothetical, only when the live chat is on.
- `/live/[eventId]` is the chat (`components/LiveChat.tsx`): the game at the top, the credits
  (`CreditsMeter`, a warning when low), what's posted (`MarketsPanel`: asked once when the game is
  opened, 1 credit, kept 5 minutes; a `<details>` open by default, the "dropdown" on a phone),
  the thread, and the box. The box keeps focus after each answer. While someone types, the cost
  line comes from `/api/live/plan` ("This will cost 1 to 2 credits."), which never reaches The Odds
  API. Each answer (`LiveAnswerView`): the plain-language reply, a price card per line (the book
  of record's price, what $10 returns, the other side, each other book), "Live price as of
  6:11 PM · from the last minute's prices · no credits used". A bet the books don't post is a
  plain "Market not available" reply with no price card ("Checked at …", not "Live price").
- A price a minute old or older (`STALE_AFTER_MS`, the odds' own limit) is dashed, and says it may
  have moved, with "Ask again".
- The thread is kept in `localStorage` per game (`lib/liveChat.ts`), at most 40 turns; a question
  cut off by closing the page says so; the saved chat of a game that has left the schedule is
  dropped when the front page loads (`ThreadTidy`). Nothing is stored on the server.
- Links to a game are `prefetch={false}`: opening one lists its bets, which costs a credit.
- Colours only from `lib/tokens.ts` (one pairing added: orange-bright on the warning tint, the
  "Ask again" link). `e2e/live.spec.ts` runs the contrast, text-over-the-world and 44 px audit
  and axe on the live pages (the audit helpers moved to `e2e/auditLib.ts`).
- `LIVE_ODDS_FIXTURE=1` (`backend/juju/dev_live.py`, dev and CI only) answers the live chat from
  the recordings with no key and no credits, so Playwright can drive it. CI starts a second site
  with a passcode for `e2e/live.spec.ts`.
- **No visual snapshots of the live pages yet:** `scripts/visual.sh` needs Docker, which the
  cloud container didn't have running, so the pictures would not match CI's. Take them with
  `scripts/visual.sh --update` once, where Docker runs, with the live site on :3002.
- Not done, on purpose: "How it works" (`/about`) still describes the archive only; the root
  page `<title>` and description are the archive's.

## Step 3: the assistant (built)

**What DeepSeek's own docs say** (read 2026-11-20 at api-docs.deepseek.com; re-read before relying
on any of it, they say prices may change):
- The current model is `deepseek-flash` (served by DeepSeek-V4.1-Flash-1017); the older
  `deepseek-v4-flash` is still accepted and billed the same. 1M context. Tool calls and JSON output
  are listed as supported.
- Tool calls are the OpenAI format: `tools` of `{"type":"function","function":{name, description,
  parameters}}`; the reply's `choices[0].message.tool_calls` have `id` and `function.arguments`
  (a JSON string that may be invalid); the results go back as `role: "tool"` messages with the
  `tool_call_id`. `strict` tool schemas are a beta on another base URL: not used.
- **Thinking is on by default**, and with tools the model's `reasoning_content` must be passed
  back or the API answers 400. The assistant turns it off (`"thinking": {"type": "disabled"}`,
  through `LLM_EXTRA_BODY`), and passes `reasoning_content` back anyway if it is ever on.
- JSON output is `response_format: {"type": "json_object"}`; `json_schema` is not documented. So
  the free-text parse fallback (`ingest/llm.py`) gained `LLM_RESPONSE_FORMAT=json_object`, which
  puts the schema in the prompt, and `LLM_EXTRA_BODY`.
- Price per million tokens for Flash: $0.15 input (cache miss; $0.003 on a hit) and $0.60 output,
  double during peak hours (01:00 to 04:00 and 06:00 to 10:00 UTC on weekdays). A question is a
  fraction of a cent: the 300 a day cap is about caution, not cost. 429 is a concurrency limit
  (2,500), 402 is no balance; both end the question and the chat goes on from templates.

**How it works**
- `api/live_chat.py` `_ask`: a message the parser can read as a bet (or several) runs the plain
  pipeline: exact cost, no model call. A message that asks for a pick ("should I take the over?")
  gets the price with "Juju doesn't give picks" in front, or just the refusal; no model call. Only
  the rest ("and the under?", "what does +180 mean?", "how is he doing") goes to the assistant,
  and only when it is on: `LIVE_ASSISTANT=1` with `LLM_API_KEY` and `LLM_MODEL`. The parse
  fallback's key alone never turns it on.
- `ingest/assistant.py`: the loop. One OpenAI-format chat completions call at a time, temperature
  0, 400 tokens; at most 4 tool calls run per question (the rest are told so, and the model is
  then told to answer: `tool_choice: none`); 5 model calls and 15 seconds in all; a daily budget of
  questions (`LIVE_ASSISTANT_DAILY`, 300) counted by `Budget`. 402, 429, 5xx, a timeout or a bad
  reply each end the question with a status, never an exception. Only the kind of failure is
  logged: never the words, never the key.
- `api/live_assistant.py`: the five tools, which are the only way the model learns a number.
  `find_player` and `get_player_stat` are free; `list_markets`, `get_live_price` and `price_parlay`
  use the same 60-second cache and a **ceiling of 6 credits for the whole question**, held by the
  one `_fetch` that counts them (a cached market is free). No tool takes "all": a price needs a
  named player or team, a parlay its 2 to 6 legs, and a name that matches nobody is an error.
- `core/assistant.py`: what it may say. **Every number in its words must be in a tool result from
  this question or in the person's own message**; a price must match its sign (+125 is not -125);
  plain counts up to twelve and the $10 stake are free. **Numbers from earlier turns do not
  count**, because the browser can write anything there; a forged system turn is refused (422),
  and at most six turns of 500 characters are taken. No advice, predictions, "good bet", links;
  at most 700 characters; markdown stripped. The model is told to answer `[[REFUSE]]` for anything
  but this game's bets and stats, and the server swaps in fixed words.
- When a check fails, the answer is the template one built from what the tools priced ("Market not
  available" if they priced nothing and a bet had no posted line), or the plain "I couldn't tell
  which bet that is" if they priced nothing. The cards on the page are always the server's, from
  the legs the tools priced; an accepted reply is tagged "AI-written reply".
- The cost line says "This will use up to 6 credits, and often none" for a message that may go to
  the assistant, and "No credits are used until you name a bet" when there is none.
- The site sends the last three answered exchanges as `history`; the proxy's body limit for live
  questions is 8,000 characters (a lookup's is still 1,000). The privacy page says that a message
  the chat can't read itself, and the last few, go to DeepSeek when the assistant is on.
- Tests: `tests/unit/test_assistant_guard.py` (the rules), `test_assistant.py` (the loop, against a
  scripted fake model with httpx's `MockTransport`), `tests/db/test_live_assistant.py` (end to
  end: a follow-up, an invented price, a wrong sign, advice, a refusal, a prompt injection asking
  for every line, forged history, the credit ceiling, the daily cap, a model that is down), and
  `e2e/live.spec.ts` (the history sent, the label, the audit).
- **Not done:** a real call to DeepSeek (no key, and the cloud container's network allows only
  listed hosts: `api.deepseek.com` was never added). The request and response shapes follow its
  docs and are tested against a fake; the first real question will show what the docs missed.
  Streaming (the checks need the whole reply). A check on whether Flash follows the "ask one short
  question" rule well; the guards make a bad reply harmless, not good.

## Risks the owner accepted, and ones to watch

- **Credits.** Live questions come before the archive's captures, with no per-visitor cap. A busy
  night can leave T-45 captures without prices (item 7 above). The one real protection is the
  60-second cache; the live meter warns at 2,000.
- **Pre-kickoff rosters.** A player's position ("yards" with no kind) and "where he stands" come
  from the database, which knows only games the worker follows. For others the chat asks which
  kind of yards, and says nothing about the box score.
- **`/events/{id}/markets`** is billed at 1 credit per The Odds API's documentation; the first real
  call's `x-requests-last` header confirms it. Nothing assumes it beyond the cost shown.
- **The worker's own count** of credits updates on its next call: the live chat writes the credits
  left to `source_health`, but the worker's in-memory copy catches up on its next reply.
