// The passcode gate (lib/passcode.ts), on only when SITE_PASSCODE is set, as on the private
// preview (deploy/laptop/). Without it, every request goes straight through.
import { NextResponse, type NextRequest } from "next/server";
import { PASS_COOKIE, gate } from "@/lib/passcode";
import { unlockHtml } from "@/lib/unlockPage";

const NO_STORE = { "cache-control": "no-store", "x-robots-tag": "noindex" };

export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const verdict = gate(pathname, request.cookies.get(PASS_COOKIE)?.value,
                       process.env.SITE_PASSCODE, Date.now());
  if (verdict === "open") return NextResponse.next();
  if (verdict === "locked") {
    return NextResponse.json({ detail: "Enter the passcode first.", locked: true },
                             { status: 401, headers: NO_STORE });
  }
  return new NextResponse(unlockHtml(pathname + search, false), {
    status: 401, headers: { ...NO_STORE, "content-type": "text/html; charset=utf-8" },
  });
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|robots.txt).*)"],
};
