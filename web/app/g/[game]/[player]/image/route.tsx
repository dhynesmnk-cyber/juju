// The share image for a player's card: /g/{game}/{player}/image?card={market}[&threshold=]
import type { NextRequest } from "next/server";
import { backendGet } from "@/lib/backend";
import { CARD_KEY, THRESHOLD, imageCache, pickCard, shareImage } from "@/lib/shareImage";
import type { PlayerView } from "@/lib/types";

type Ctx = { params: Promise<{ game: string; player: string }> };

export async function GET(req: NextRequest, ctx: Ctx) {
  const { game, player } = await ctx.params;
  const card = req.nextUrl.searchParams.get("card");
  const threshold = req.nextUrl.searchParams.get("threshold");
  if (!/^\d+$/.test(game) || !/^\d+$/.test(player) || (card && !CARD_KEY.test(card))
      || (threshold && !THRESHOLD.test(threshold))) {
    return new Response(null, { status: 404 });
  }
  const view = await backendGet<PlayerView>(
    `/api/player/${game}/${player}${threshold ? `?threshold=${threshold}` : ""}`);
  if (!view) return new Response(null, { status: 404 });
  const chosen = pickCard(view.cards, card);
  return shareImage(view.player.name, view.game, chosen, imageCache(view.game, chosen));
}
