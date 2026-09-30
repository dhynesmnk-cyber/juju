import type { Metadata } from "next";
import { notFound } from "next/navigation";
import ResultView from "@/components/ResultView";
import { backendGet } from "@/lib/backend";
import { CARD_KEY, SIZE, imagePath, pickCard } from "@/lib/shareImage";
import type { TeamView } from "@/lib/types";

export const dynamic = "force-dynamic";

type Props = {
  params: Promise<{ game: string; team: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

async function load(props: Props) {
  const { game, team } = await props.params;
  if (!/^\d+$/.test(game) || !/^\d+$/.test(team)) return null;
  const sp = await props.searchParams;
  const view = await backendGet<TeamView>(`/api/team/${game}/${team}`);
  const pin = typeof sp.card === "string" && CARD_KEY.test(sp.card) ? sp.card : undefined;
  return view ? { view, apiPath: `/api/team/${game}/${team}`,
                  pagePath: `/g/${game}/team/${team}`, pin } : null;
}

export async function generateMetadata(props: Props): Promise<Metadata> {
  const data = await load(props);
  if (!data) return { title: "Juju" };
  const top = pickCard(data.view.cards, data.pin ?? null);
  const what = top ? `${top.bet}: ${top.headline}` : "No archived prices";
  const title = `${data.view.team.name}, ${data.view.game.label} | Juju`;
  const description = `${what}. What $10 at the price 45 minutes before kickoff would have paid.`;
  const image = { url: imagePath(data.pagePath, top), ...SIZE, alt: `${title}: ${what}` };
  return { title, description, openGraph: { title, description, images: [image] },
           twitter: { card: "summary_large_image", title, description, images: [image] } };
}

export default async function TeamPage(props: Props) {
  const data = await load(props);
  if (!data) notFound();
  return <ResultView initial={data.view} apiPath={data.apiPath} pagePath={data.pagePath}
                     pin={data.pin} />;
}
