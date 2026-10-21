import { NextResponse, type NextRequest } from "next/server";
import {
  TRACKER_COOKIE, TRACKER_SECONDS, matchOwner, parseOwners, safeMeNext, signOwner,
} from "@/lib/trackerPass";
import { trackerUnlockHtml } from "@/lib/unlockPage";

// Slows guessing down; the passcodes themselves should be long (deploy/laptop/up.sh makes them).
const WRONG_DELAY_MS = 1000;

/** The owners' sign-in form (lib/unlockPage.ts). Right: a 30-day cookie for that owner and on
 *  to the page asked for. Wrong: the form again, saying so. */
export async function POST(req: NextRequest) {
  const owners = parseOwners(process.env.TRACKER_USERS);
  if (!owners) return new NextResponse("Not found", { status: 404 });
  const form = await req.formData().catch(() => null);
  const next = safeMeNext(String(form?.get("next") ?? ""));
  const name = String(form?.get("name") ?? "");
  const attempt = String(form?.get("passcode") ?? "");
  const owner = name.length <= 32 && attempt.length <= 200
    ? matchOwner(owners, name, attempt) : null;
  if (!owner) {
    await new Promise((resolve) => setTimeout(resolve, WRONG_DELAY_MS));
    return new NextResponse(trackerUnlockHtml(next, true), {
      status: 401,
      headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store",
                 "x-robots-tag": "noindex" },
    });
  }
  const res = seeOther(next);
  res.cookies.set(TRACKER_COOKIE, signOwner(owner, owners.get(owner) ?? "", Date.now()), {
    httpOnly: true, secure: true, sameSite: "lax", path: "/", maxAge: TRACKER_SECONDS,
  });
  return res;
}

/** A relative Location: behind a tunnel, the request's own URL can name the wrong host. */
function seeOther(location: string): NextResponse {
  return new NextResponse(null, {
    status: 303, headers: { location, "cache-control": "no-store" },
  });
}
