import type { Metadata } from "next";
import { notFound } from "next/navigation";
import GamePage from "@/components/GamePage";
import { backendPage } from "@/lib/backend";
import type { GamePageView } from "@/lib/types";

export const dynamic = "force-dynamic";

type Props = { params: Promise<{ game: string }> };

async function load(props: Props): Promise<GamePageView | null> {
  const { game } = await props.params;
  if (!/^\d+$/.test(game)) return null;
  return backendPage<GamePageView>(`/api/game/${game}`);
}

export async function generateMetadata(props: Props): Promise<Metadata> {
  const g = await load(props);
  if (!g) return { title: "Juju" };
  const title = `${g.label} | Juju`;
  const description = "Every play, and what $10 at the price 45 minutes before kickoff would have paid.";
  return { title, description, openGraph: { title, description } };
}

export default async function Game(props: Props) {
  const g = await load(props);
  if (!g) notFound();
  return <GamePage initial={g} />;
}
