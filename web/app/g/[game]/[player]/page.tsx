import type { Metadata } from "next";
import { notFound } from "next/navigation";
import ResultView from "@/components/ResultView";
import { backendGet } from "@/lib/backend";
import type { PlayerView } from "@/lib/types";

export const dynamic = "force-dynamic";

type Props = {
  params: Promise<{ game: string; player: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

const KEEP = ["play", "market", "threshold", "yards", "expect"];

function query(sp: Record<string, string | string[] | undefined>): string {
  const q = new URLSearchParams();
  for (const k of KEEP) {
    const v = sp[k];
    if (typeof v === "string" && v) q.set(k, k === "expect" ? "true" : v);
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

async function load(props: Props) {
  const { game, player } = await props.params;
  if (!/^\d+$/.test(game) || !/^\d+$/.test(player)) return null;
  const q = query(await props.searchParams);
  const view = await backendGet<PlayerView>(`/api/player/${game}/${player}${q}`);
  return view ? { view, apiPath: `/api/player/${game}/${player}${q}` } : null;
}

export async function generateMetadata(props: Props): Promise<Metadata> {
  const data = await load(props);
  if (!data) return { title: "Juju" };
  const top = data.view.cards[0];
  const what = top ? `${top.bet}: ${top.headline}` : "No archived prices";
  return {
    title: `${data.view.player.name}, ${data.view.game.label} | Juju`,
    description: `${what}. What $10 at the price 45 minutes before kickoff would have paid.`,
  };
}

export default async function PlayerPage(props: Props) {
  const data = await load(props);
  if (!data) notFound();
  return <ResultView initial={data.view} apiPath={data.apiPath} />;
}
