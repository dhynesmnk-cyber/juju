// The owners' own API (/api/me): its own door, apart from the public one, so none of it is ever
// on the public allowlist (lib/publicApi.ts). proxy.ts has already checked the owner's cookie;
// it is checked again here, and only the owner's name reaches the backend, as `x-juju-user`.
// Nothing here is cached anywhere: a recap lists prices across players, for its owner only
// (docs/licensing.md).
import { type NextRequest, NextResponse } from "next/server";
import { BACKEND_URL } from "@/lib/backend";
import { TRACKER_COOKIE, ownerOf, parseOwners } from "@/lib/trackerPass";

type Method = "GET" | "POST" | "DELETE";
const PATHS: Record<Method, RegExp[]> = {
  GET: [/^recaps$/, /^recaps\/\d+$/, /^history\/(player|team)\/\d+$/],
  POST: [/^lookups$/],
  DELETE: [/^lookups\/\d+$/],
};
const PRIVATE = { "cache-control": "no-store", "x-robots-tag": "noindex" };

type Ctx = { params: Promise<{ path: string[] }> };

async function forward(req: NextRequest, ctx: Ctx, method: Method): Promise<NextResponse> {
  const path = (await ctx.params).path.join("/");
  const owners = parseOwners(process.env.TRACKER_USERS);
  if (!owners || !PATHS[method].some((p) => p.test(path))) {
    return NextResponse.json({}, { status: 404, headers: PRIVATE });
  }
  const owner = ownerOf(req.cookies.get(TRACKER_COOKIE)?.value, owners, Date.now());
  if (!owner) {
    return NextResponse.json({ detail: "Sign in first.", locked: true },
                             { status: 401, headers: PRIVATE });
  }
  const body = method === "POST" ? await req.text() : undefined;
  if (body !== undefined && body.length > 2000) {
    return NextResponse.json({}, { status: 413, headers: PRIVATE });
  }
  try {
    const r = await fetch(`${BACKEND_URL}/api/me/${path}${req.nextUrl.search}`, {
      method, body, cache: "no-store",
      headers: { "content-type": "application/json", "x-juju-user": owner },
      signal: AbortSignal.timeout(15000),
    });
    return new NextResponse(await r.text(), {
      status: r.status,
      headers: { ...PRIVATE, "content-type": r.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json({ detail: "Juju's backend is unreachable right now." },
                             { status: 503, headers: PRIVATE });
  }
}

export async function GET(req: NextRequest, ctx: Ctx) { return forward(req, ctx, "GET"); }
export async function POST(req: NextRequest, ctx: Ctx) { return forward(req, ctx, "POST"); }
export async function DELETE(req: NextRequest, ctx: Ctx) { return forward(req, ctx, "DELETE"); }
