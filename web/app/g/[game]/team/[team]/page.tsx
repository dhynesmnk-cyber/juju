import type { Metadata } from "next";
import { notFound } from "next/navigation";
import ResultView from "@/components/ResultView";
import { backendGet } from "@/lib/backend";
import type { TeamView } from "@/lib/types";

export const dynamic = "force-dynamic";

type Props = { params: Promise<{ game: string; team: string }> };

async function load(props: Props) {
  const { game, team } = await props.params;
  if (!/^\d+$/.test(game) || !/^\d+$/.test(team)) return null;
  const view = await backendGet<TeamView>(`/api/team/${game}/${team}`);
  return view ? { view, apiPath: `/api/team/${game}/${team}` } : null;
}

export async function generateMetadata(props: Props): Promise<Metadata> {
  const data = await load(props);
  return { title: data ? `${data.view.team.name}, ${data.view.game.label} | Juju` : "Juju" };
}

export default async function TeamPage(props: Props) {
  const data = await load(props);
  if (!data) notFound();
  return <ResultView initial={data.view} apiPath={data.apiPath} />;
}
