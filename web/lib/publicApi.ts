// The public proxy's allowlist (app/api/[...path]/route.ts): the only backend endpoints anyone
// can reach, and none of them the tracker's /api/me (docs/licensing.md). No "@/" imports: `npm
// test` runs the lib tests with Node's own test runner.
export const GET_PATHS = [/^live$/, /^status$/, /^suggest$/, /^player\/\d+\/\d+$/,
                          /^team\/\d+\/\d+$/, /^parlay\/\d+$/];
export const POST_PATHS = [/^lookup$/];

/** Whether the public proxy forwards this path (after /api/) for this method. */
export function isPublicPath(method: "GET" | "POST", path: string): boolean {
  return (method === "GET" ? GET_PATHS : POST_PATHS).some((p) => p.test(path));
}
