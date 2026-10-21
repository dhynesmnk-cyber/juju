// The passcode gate (lib/passcode.ts), on only when SITE_PASSCODE is set, as on the private
// preview (deploy/laptop/). Without it, every request goes straight through. Then the owners'
// own section (/me, /api/me: lib/trackerPass.ts), which also needs an owner's own sign-in, and
// isn't there at all unless TRACKER_USERS is set.
import { NextResponse, type NextRequest } from "next/server";
import { PASS_COOKIE, gate } from "@/lib/passcode";
import { TRACKER_COOKIE, parseOwners, trackerGate } from "@/lib/trackerPass";
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
  const me = trackerGate(pathname, request.cookies.get(TRACKER_COOKIE)?.value,
                         parseOwners(process.env.TRACKER_USERS), now);
  if (me === "pass" || me === "open") return NextResponse.next();
  if (me === "off") return new NextResponse("Not found", { status: 404, headers: NO_STORE });
  if (me === "locked") {
    return NextResponse.json({ detail: "Sign in first.", locked: true },
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
