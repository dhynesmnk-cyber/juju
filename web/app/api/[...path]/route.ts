// The only way into the backend. It forwards the few UI endpoints (and nothing else) over the
// private network, passing the backend's Cache-Control through so Cloudflare can cache results
// for a few seconds. The backend itself has no public address (docs/licensing.md).
import { type NextRequest, NextResponse } from "next/server";
import { BACKEND_URL } from "@/lib/backend";

const GET_PATHS = [/^live$/, /^status$/, /^suggest$/, /^player\/\d+\/\d+$/, /^team\/\d+\/\d+$/];
const POST_PATHS = [/^lookup$/];

function clientId(req: NextRequest): string {
  return req.headers.get("cf-connecting-ip") ?? req.headers.get("fly-client-ip")
    ?? req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ?? "unknown";
}

async function forward(req: NextRequest, path: string, init: RequestInit): Promise<Response> {
  const url = `${BACKEND_URL}/api/${path}${req.nextUrl.search}`;
  try {
    const r = await fetch(url, {
      ...init, cache: "no-store",
      headers: { "content-type": "application/json", "x-juju-client": clientId(req) },
      signal: AbortSignal.timeout(8000),
    });
    const body = await r.text();
    return new NextResponse(body, {
      status: r.status,
      headers: {
        "content-type": r.headers.get("content-type") ?? "application/json",
        "cache-control": r.headers.get("cache-control") ?? "no-store",
      },
    });
  } catch {
    return NextResponse.json({ detail: "Juju's backend is unreachable right now." },
                             { status: 503, headers: { "cache-control": "no-store" } });
  }
}

type Ctx = { params: Promise<{ path: string[] }> };

export async function GET(req: NextRequest, ctx: Ctx) {
  const path = (await ctx.params).path.join("/");
  if (!GET_PATHS.some((p) => p.test(path))) return NextResponse.json({}, { status: 404 });
  return forward(req, path, { method: "GET" });
}

export async function POST(req: NextRequest, ctx: Ctx) {
  const path = (await ctx.params).path.join("/");
  if (!POST_PATHS.some((p) => p.test(path))) return NextResponse.json({}, { status: 404 });
  const text = await req.text();
  if (text.length > 1000) return NextResponse.json({}, { status: 413 });
  return forward(req, path, { method: "POST", body: text });
}
