// The public API's allowlist: the few UI endpoints app/api/[...path]/route.ts forwards, and
// nothing else. The owners' own API (/api/me) has its own door and is never on it. No "@/"
// imports: `npm test` runs the lib tests with Node's own test runner.
const GET_PATHS = [/^live$/, /^status$/, /^suggest$/, /^game\/\d+$/, /^player\/\d+\/\d+$/,
                   /^team\/\d+\/\d+$/, /^parlay\/\d+$/];
const POST_PATHS = [/^lookup$/];

// The live chat (docs/plans/live-chat.md): live odds on demand, the one door where a person's
// question reaches The Odds API. It spends credits, with no per-visitor cap, so it exists only
// where a passcode stands in front of the site (`liveChatOpen`).
const LIVE_GET_PATHS = [/^live\/games$/, /^live\/markets$/, /^live\/plan$/, /^live-markets$/];
const LIVE_POST_PATHS = [/^live\/ask$/];

/** The most a live question may weigh: its text, and the last few turns of the chat. */
export const LIVE_BODY_LIMIT = 8000;

/** Is this path the live chat's? (`live` alone is the archive's home-page list.) */
export function isLiveChat(path: string): boolean {
  return /^live[/-]/.test(path);
}

/** The live chat is on only when the site has a passcode (SITE_PASSCODE): without one, anyone
 *  on the internet could spend the owner's credits. */
export function liveChatOpen(passcode: string | undefined): boolean {
  return Boolean(passcode);
}

export function isPublicApi(method: "GET" | "POST", path: string): boolean {
  const live = method === "GET" ? LIVE_GET_PATHS : LIVE_POST_PATHS;
  return (method === "GET" ? GET_PATHS : POST_PATHS).concat(live).some((p) => p.test(path));
}
