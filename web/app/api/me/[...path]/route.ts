// The only way into the tracker's API, /api/me (backend/juju/api/me.py), and kept apart from the
// public proxy (app/api/[...path]/route.ts), whose allowlist never names it. proxy.ts has already
// checked the cookie; this checks it again, and tells the backend who it is with `x-juju-user`,
// the one header the backend trusts for it (it has no public address). Answers are never cached:
// slips carry stakes.
import { type NextRequest, NextResponse } from "next/server";
import { BACKEND_URL } from "@/lib/backend";
import { TRACKER_COOKIE, parseTrackerUsers, trackerUserFrom } from "@/lib/trackerUsers";

const NO_STORE = { "cache-control": "no-store" };
// Each part of the path, so nothing like ".." can leave /api/me on the backend.
const SEGMENT = /^[A-Za-z0-9_-]{1,40}$/;
const MAX_BODY = 64 * 1024;  // a slip is a few kilobytes; the screenshot route will need its own
// Saving a slip may look its games up on ESPN, which can wait 5 s for a slot (pickers.py).
const TIMEOUT_MS = 20_000;

type Ctx = { params: Promise<{ path: string[] }> };

async function forward(req: NextRequest, ctx: Ctx, method: "GET" | "POST" | "PATCH") {
  const { users } = parseTrackerUsers(process.env.TRACKER_USERS);
  if (users.length === 0) {
    return NextResponse.json({ detail: "Not Found" }, { status: 404, headers: NO_STORE });
  }
  const user = trackerUserFrom(req.cookies.get(TRACKER_COOKIE)?.value, users, Date.now());
  if (!user) {
    return NextResponse.json({ detail: "Sign in to Watch my parlays first.", locked: true },
                             { status: 401, headers: NO_STORE });
  }
  const segments = (await ctx.params).path;
  if (!segments.every((s) => SEGMENT.test(s))) {
    return NextResponse.json({ detail: "Not Found" }, { status: 404, headers: NO_STORE });
  }
  let body: string | undefined;
  if (method !== "GET") {
    body = await req.text();
    if (body.length > MAX_BODY) {
      return NextResponse.json({ detail: "Too large." }, { status: 413, headers: NO_STORE });
    }
  }
  try {
    const r = await fetch(`${BACKEND_URL}/api/me/${segments.join("/")}${req.nextUrl.search}`, {
      method, body, cache: "no-store",
      headers: { "content-type": "application/json", "x-juju-user": user },
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    return new NextResponse(await r.text(), {
      status: r.status,
      headers: { "content-type": r.headers.get("content-type") ?? "application/json",
                 ...NO_STORE },
    });
  } catch {
    return NextResponse.json({ detail: "Juju's backend is unreachable right now." },
                             { status: 503, headers: NO_STORE });
  }
}

export async function GET(req: NextRequest, ctx: Ctx) {
  return forward(req, ctx, "GET");
}

export async function POST(req: NextRequest, ctx: Ctx) {
  return forward(req, ctx, "POST");
}

export async function PATCH(req: NextRequest, ctx: Ctx) {
  return forward(req, ctx, "PATCH");
}
