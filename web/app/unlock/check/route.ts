import { NextResponse, type NextRequest } from "next/server";
import {
  PASS_COOKIE, PASS_SECONDS, passcodeMatches, safeNext, unlockPass,
} from "@/lib/passcode";
import { unlockHtml } from "@/lib/unlockPage";

// Slows guessing down; the passcode itself should be long (deploy/laptop/up.sh makes one).
const WRONG_DELAY_MS = 1000;

/** The passcode form (lib/unlockPage.ts). Right: a 30-day cookie and on to the page asked
 *  for. Wrong: the form again, saying so. */
export async function POST(req: NextRequest) {
  const form = await req.formData().catch(() => null);
  const next = safeNext(String(form?.get("next") ?? ""));
  const passcode = process.env.SITE_PASSCODE;
  if (!passcode) return seeOther(next);
  const attempt = String(form?.get("passcode") ?? "");
  if (attempt.length > 200 || !passcodeMatches(passcode, attempt)) {
    await new Promise((resolve) => setTimeout(resolve, WRONG_DELAY_MS));
    return new NextResponse(unlockHtml(next, true), {
      status: 401,
      headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store",
                 "x-robots-tag": "noindex" },
    });
  }
  const res = seeOther(next);
  res.cookies.set(PASS_COOKIE, unlockPass(passcode, Date.now()), {
    httpOnly: true, secure: true, sameSite: "lax", path: "/", maxAge: PASS_SECONDS,
  });
  return res;
}

/** A relative Location: behind a tunnel, the request's own URL can name the wrong host. */
function seeOther(location: string): NextResponse {
  return new NextResponse(null, {
    status: 303, headers: { location, "cache-control": "no-store" },
  });
}
