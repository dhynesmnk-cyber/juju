// The share image for a parlay: /g/{game}/parlay/image?legs=... It asks the parlay's own
// endpoint for exactly the legs in the address, as the page does, and nothing else.
import type { NextRequest } from "next/server";
import { backendGet } from "@/lib/backend";
import { LEGS, imageCache, parlayShareImage } from "@/lib/shareImage";
import type { ParlayView } from "@/lib/types";

type Ctx = { params: Promise<{ game: string }> };

export async function GET(req: NextRequest, ctx: Ctx) {
  const { game } = await ctx.params;
  const legs = req.nextUrl.searchParams.get("legs");
  if (!/^\d+$/.test(game) || !legs || !LEGS.test(legs)) {
    return new Response(null, { status: 404 });
  }
  const view = await backendGet<ParlayView>(
    `/api/parlay/${game}?legs=${encodeURIComponent(legs)}`);
  if (!view) return new Response(null, { status: 404 });
  return parlayShareImage(view, imageCache(view.game, view.outcome));
}
