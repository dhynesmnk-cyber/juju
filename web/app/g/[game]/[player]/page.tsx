import type { Metadata } from "next";
import { notFound } from "next/navigation";
import ResultView from "@/components/ResultView";
import { backendGet } from "@/lib/backend";
import { CARD_KEY, SIZE, imagePath, pickCard } from "@/lib/shareImage";
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
  const sp = await props.searchParams;
  const q = query(sp);
  const view = await backendGet<PlayerView>(`/api/player/${game}/${player}${q}`);
  const pin = typeof sp.card === "string" && CARD_KEY.test(sp.card) ? sp.card : undefined;
  return view ? { view, apiPath: `/api/player/${game}/${player}${q}`,
                  pagePath: `/g/${game}/${player}`, pin } : null;
}

export async function generateMetadata(props: Props): Promise<Metadata> {
  const data = await load(props);
  if (!data) return { title: "Juju" };
  const top = pickCard(data.view.cards, data.pin ?? null);
  const what = top ? `${top.bet}: ${top.headline}` : "No archived prices";
  const title = `${data.view.player.name}, ${data.view.game.label} | Juju`;
  const description = `${what}. What $10 at the price 45 minutes before kickoff would have paid.`;
  const image = { url: imagePath(data.pagePath, top), ...SIZE, alt: `${title}: ${what}` };
  return { title, description, openGraph: { title, description, images: [image] },
           twitter: { card: "summary_large_image", title, description, images: [image] } };
}

export default async function PlayerPage(props: Props) {
  const data = await load(props);
  if (!data) notFound();
  return <ResultView initial={data.view} apiPath={data.apiPath} pagePath={data.pagePath}
                     pin={data.pin} />;
}
