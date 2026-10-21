// The public API's allowlist: the few UI endpoints app/api/[...path]/route.ts forwards, and
// nothing else. The owners' own API (/api/me) has its own door and is never on it. No "@/"
// imports: `npm test` runs the lib tests with Node's own test runner.
const GET_PATHS = [/^live$/, /^status$/, /^suggest$/, /^game\/\d+$/, /^player\/\d+\/\d+$/,
                   /^team\/\d+\/\d+$/, /^parlay\/\d+$/];
const POST_PATHS = [/^lookup$/];

export function isPublicApi(method: "GET" | "POST", path: string): boolean {
  return (method === "GET" ? GET_PATHS : POST_PATHS).some((p) => p.test(path));
}
