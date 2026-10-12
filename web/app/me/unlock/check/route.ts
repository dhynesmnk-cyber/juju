import { NextResponse, type NextRequest } from "next/server";
import {
  TRACKER_COOKIE, TRACKER_SECONDS, parseTrackerUsers, signIn, trackerNext, trackerPass,
} from "@/lib/trackerUsers";
import { trackerUnlockHtml } from "@/lib/unlockPage";

// Slows guessing down; each passcode itself is at least 10 characters (lib/trackerUsers.ts).
const WRONG_DELAY_MS = 1000;

/** The "Watch my parlays" sign-in form (lib/unlockPage.ts). Right: a 30-day cookie naming the
 *  user, and on to the /me page asked for. Wrong: the form again, saying so. */
export async function POST(req: NextRequest) {
  const { users, problems } = parseTrackerUsers(process.env.TRACKER_USERS);
  if (users.length === 0) {
    return NextResponse.json({ detail: "Not Found" }, { status: 404 });
  }
  const form = await req.formData().catch(() => null);
  const next = trackerNext(String(form?.get("next") ?? ""));
  const name = String(form?.get("name") ?? "");
  const passcode = String(form?.get("passcode") ?? "");
  const user = name.length <= 40 && passcode.length <= 200 ? signIn(users, name, passcode) : null;
  if (!user) {
    // For whoever runs the site (`docker compose logs web`): never a passcode, only positions.
    if (problems.length) console.warn(`Watch my parlays sign-in failed; ${problems.join("; ")}`);
    await new Promise((resolve) => setTimeout(resolve, WRONG_DELAY_MS));
    return new NextResponse(trackerUnlockHtml(next, true), {
      status: 401,
      headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store",
                 "x-robots-tag": "noindex" },
    });
  }
  // A relative Location: behind a tunnel, the request's own URL can name the wrong host.
  const res = new NextResponse(null, {
    status: 303, headers: { location: next, "cache-control": "no-store" },
  });
  res.cookies.set(TRACKER_COOKIE, trackerPass(user, Date.now()), {
    httpOnly: true, secure: true, sameSite: "lax", path: "/", maxAge: TRACKER_SECONDS,
  });
  return res;
}
