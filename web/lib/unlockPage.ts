// The passcode page of a private preview (proxy.ts): plain HTML and one form, no app code. It
// is served at whatever address was asked for, so there is no redirect (which, from proxy.ts,
// needs an absolute URL a tunnel can get wrong) and nothing for the app's router to prefetch.
// No "@/" imports: `npm test` runs the lib tests with Node's own test runner.
import { CHECK_PATH } from "./passcode.ts";

function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

export function unlockHtml(next: string, wrong: boolean): string {
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Private preview | Juju</title>
<style>
  :root { color-scheme: light dark; --bg: #f6f7f9; --fg: #14171c; --muted: #555d6b;
          --field: #ffffff; --line: #c9ced8; --accent: #2f5bd3; --accent-fg: #ffffff; --bad: #a8261b; }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #0d1015; --fg: #eef1f6; --muted: #a3acbb; --field: #161b23; --line: #2b3342;
            --accent: #8fb0ff; --accent-fg: #0d1015; --bad: #ff8f85; }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--fg);
         font: 17px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  main { max-width: 26rem; margin: 0 auto; padding: 48px 16px; }
  .brand { font-weight: 900; font-size: 1.6rem; letter-spacing: -0.02em; }
  .brand span { color: var(--accent); }
  h1 { font-size: 1.5rem; margin: 28px 0 8px; }
  p { margin: 0 0 16px; color: var(--muted); }
  label { display: block; font-weight: 700; margin-bottom: 6px; }
  .row { display: flex; gap: 8px; }
  input { flex: 1; min-width: 0; min-height: 48px; padding: 0 12px; font: inherit; color: var(--fg);
          background: var(--field); border: 1px solid var(--line); border-radius: 10px; }
  button { min-height: 48px; padding: 0 20px; font: inherit; font-weight: 700; color: var(--accent-fg);
           background: var(--accent); border: 0; border-radius: 10px; cursor: pointer; }
  input:focus-visible, button:focus-visible { outline: 3px solid var(--accent); outline-offset: 2px; }
  .wrong { color: var(--bad); font-weight: 600; margin-top: 12px; }
  footer { margin-top: 48px; font-size: 0.85rem; }
</style>
</head>
<body>
<main>
  <div class="brand" aria-label="Juju">ju<span>ju</span></div>
  <h1>Private preview</h1>
  <p>Juju isn't open to the public yet. Enter the passcode you were given.</p>
  <form method="post" action="${CHECK_PATH}">
    <input type="hidden" name="next" value="${escapeHtml(next)}">
    <label for="passcode">Passcode</label>
    <div class="row">
      <input id="passcode" name="passcode" type="password" autocomplete="current-password"
             required maxlength="200" autofocus>
      <button type="submit">Open</button>
    </div>
    ${wrong ? '<p class="wrong" role="alert">That passcode didn\'t work. Try it again.</p>' : ""}
  </form>
  <footer>
    <p>Juju is not a sportsbook. 21+ only. Gambling problem? Call 1-800-GAMBLER.</p>
  </footer>
</main>
</body>
</html>
`;
}
