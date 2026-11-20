import type { Metadata } from "next";
import { notFound } from "next/navigation";
import LiveChat from "@/components/LiveChat";
import { backendGet } from "@/lib/backend";
import type { ChatGame, Credits } from "@/lib/liveChat";
import { liveChatOpen } from "@/lib/publicApi";

export const dynamic = "force-dynamic";

type Props = { params: Promise<{ eventId: string }> };

async function load(props: Props): Promise<{ game: ChatGame; credits: Credits } | null> {
  const { eventId } = await props.params;
  // Off without a passcode on the site, like its API (docs/plans/live-chat.md).
  if (!liveChatOpen(process.env.SITE_PASSCODE) || !/^[A-Za-z0-9]{6,64}$/.test(eventId)) {
    return null;
  }
  const body = await backendGet<{ games: ChatGame[]; credits: Credits }>("/api/live/games");
  const game = body?.games.find((g) => g.id === eventId);
  return body && game ? { game, credits: body.credits } : null;
}

export async function generateMetadata(props: Props): Promise<Metadata> {
  const found = await load(props);
  return { title: found ? `${found.game.label}: live odds | Juju` : "Juju" };
}

export default async function LiveGamePage(props: Props) {
  const found = await load(props);
  if (!found) notFound();
  return <LiveChat game={found.game} initialCredits={found.credits} />;
}
