// The share image for a team's card: /g/{game}/team/{team}/image?card={market}
import type { NextRequest } from "next/server";
import { backendGet } from "@/lib/backend";
import { CARD_KEY, imageCache, pickCard, shareImage } from "@/lib/shareImage";
import type { TeamView } from "@/lib/types";

type Ctx = { params: Promise<{ game: string; team: string }> };

export async function GET(req: NextRequest, ctx: Ctx) {
  const { game, team } = await ctx.params;
  const card = req.nextUrl.searchParams.get("card");
  if (!/^\d+$/.test(game) || !/^\d+$/.test(team) || (card && !CARD_KEY.test(card))) {
    return new Response(null, { status: 404 });
  }
  const view = await backendGet<TeamView>(`/api/team/${game}/${team}`);
  if (!view) return new Response(null, { status: 404 });
  const chosen = pickCard(view.cards, card);
  return shareImage(view.team.name, view.game, chosen, imageCache(view.game, chosen));
}
