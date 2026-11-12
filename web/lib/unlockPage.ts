// The passcode page of a private preview, and the owners' sign-in (proxy.ts): plain HTML and
// one form, no app code. Each is served at whatever address was asked for, so there is no
// redirect (which, from proxy.ts, needs an absolute URL a tunnel can get wrong) and nothing for
// the app's router to prefetch. No "@/" imports: `npm test` runs the lib tests with Node's own
// test runner.
import { CHECK_PATH } from "./passcode.ts";
import { cssVariables } from "./tokens.ts";
import { ME_CHECK_PATH } from "./trackerPass.ts";

function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function page(title: string, main: string): string {
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>${title} | Juju</title>
<style>
  ${cssVariables()}
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text);
         font: 17px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  main { max-width: 26rem; margin: 0 auto; padding: 48px 16px; }
  .brand { font-weight: 800; font-size: 1.6rem; letter-spacing: -0.01em; }
  .brand span { color: var(--orange); }
  h1 { font-size: 1.5rem; margin: 28px 0 8px; }
  p { margin: 0 0 16px; color: var(--muted); }
  label { display: block; font-weight: 700; margin-bottom: 6px; }
  .row { display: flex; gap: 8px; }
  input { flex: 1; min-width: 0; min-height: 48px; padding: 0 12px; font: inherit; color: var(--text);
          background: var(--surface-2); border: 1px solid var(--line-strong); border-radius: 12px; }
  button { min-height: 48px; padding: 0 20px; font: inherit; font-weight: 800; color: var(--ink);
           background: var(--orange); border: 0; border-radius: 12px; cursor: pointer; }
  input:focus-visible, button:focus-visible { outline: 3px solid var(--orange); outline-offset: 2px; }
  .wrong { color: var(--danger); font-weight: 600; margin-top: 12px; }
  .name { margin-bottom: 14px; }
  .name input { width: 100%; }
  footer { margin-top: 48px; font-size: 0.85rem; }
</style>
</head>
<body>
<main>
  <div class="brand" aria-label="Juju">ju<span>ju</span></div>
${main}
  <footer>
    <p>Juju is not a sportsbook. 21+ only. Gambling problem? Call 1-800-GAMBLER.</p>
  </footer>
</main>
</body>
</html>
`;
}

export function unlockHtml(next: string, wrong: boolean): string {
  return page("Private preview", `  <h1>Private preview</h1>
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
  </form>`);
}

/** The owners' own sign-in (lib/trackerPass.ts), for /me and its recaps. */
export function trackerUnlockHtml(next: string, wrong: boolean): string {
  return page("Your lookups", `  <h1>Your lookups</h1>
  <p>This part is for the two owners: the bets you looked up, and how they went. Sign in with
     your name and your own passcode.</p>
  <form method="post" action="${ME_CHECK_PATH}">
    <input type="hidden" name="next" value="${escapeHtml(next)}">
    <div class="name">
      <label for="name">Name</label>
      <input id="name" name="name" type="text" autocomplete="username" autocapitalize="none"
             required maxlength="32" autofocus>
    </div>
    <label for="passcode">Passcode</label>
    <div class="row">
      <input id="passcode" name="passcode" type="password" autocomplete="current-password"
             required maxlength="200">
      <button type="submit">Sign in</button>
    </div>
    ${wrong ? '<p class="wrong" role="alert">That name and passcode didn\'t match. Try it again.</p>' : ""}
  </form>`);
}
