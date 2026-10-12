// Two gates. The site passcode (lib/passcode.ts), on only when SITE_PASSCODE is set, as on the
// private preview (deploy/laptop/). Then "Watch my parlays" (lib/trackerUsers.ts): /me and
// /api/me need one of the TRACKER_USERS signed in, and without any users they don't exist.
// Without either variable, every request goes straight through, as on Fly.
import { NextResponse, type NextRequest } from "next/server";
import { PASS_COOKIE, gate } from "@/lib/passcode";
import { TRACKER_COOKIE, parseTrackerUsers, trackerGate } from "@/lib/trackerUsers";
import { trackerUnlockHtml, unlockHtml } from "@/lib/unlockPage";

const NO_STORE = { "cache-control": "no-store", "x-robots-tag": "noindex" };
const HTML = { ...NO_STORE, "content-type": "text/html; charset=utf-8" };

export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const now = Date.now();
  const verdict = gate(pathname, request.cookies.get(PASS_COOKIE)?.value,
                       process.env.SITE_PASSCODE, now);
  if (verdict === "locked") {
    return NextResponse.json({ detail: "Enter the passcode first.", locked: true },
                             { status: 401, headers: NO_STORE });
  }
  if (verdict === "form") {
    return new NextResponse(unlockHtml(pathname + search, false), { status: 401, headers: HTML });
  }
  const tracker = trackerGate(pathname, request.cookies.get(TRACKER_COOKIE)?.value,
                              parseTrackerUsers(process.env.TRACKER_USERS).users, now);
  if (tracker === "open") return NextResponse.next();
  if (tracker === "off") {
    // Pages fall through to app/me/layout.tsx, which answers with the site's own 404.
    return pathname.startsWith("/api/")
      ? NextResponse.json({ detail: "Not Found" }, { status: 404, headers: NO_STORE })
      : NextResponse.next();
  }
  if (tracker === "locked") {
    return NextResponse.json({ detail: "Sign in to Watch my parlays first.", locked: true },
                             { status: 401, headers: NO_STORE });
  }
  return new NextResponse(trackerUnlockHtml(pathname + search, false),
                          { status: 401, headers: HTML });
}

// Everything but the build's own static files. `/_next/image` stays gated: it fetches pages
// itself, so ungated it could hand out the share images.
export const config = {
  matcher: ["/((?!_next/static|favicon.ico|robots.txt).*)"],
};
