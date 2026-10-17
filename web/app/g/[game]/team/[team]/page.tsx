import type { Metadata } from "next";
import { notFound } from "next/navigation";
import MyHistory from "@/components/MyHistory";
import RememberLookup from "@/components/RememberLookup";
import ResultView from "@/components/ResultView";
import { backendPage } from "@/lib/backend";
import { currentOwner } from "@/lib/me";
import { CARD_KEY, SIZE, imagePath, pickCard } from "@/lib/shareImage";
import type { Remembered, TeamView } from "@/lib/types";

export const dynamic = "force-dynamic";

type Props = {
  params: Promise<{ game: string; team: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

async function load(props: Props) {
  const { game, team } = await props.params;
  if (!/^\d+$/.test(game) || !/^\d+$/.test(team)) return null;
  const sp = await props.searchParams;
  const q = typeof sp.play_id === "string" && /^\d+$/.test(sp.play_id)
    ? `?play_id=${sp.play_id}` : "";
  const view = await backendPage<TeamView>(`/api/team/${game}/${team}${q}`);
  const pin = typeof sp.card === "string" && CARD_KEY.test(sp.card) ? sp.card : undefined;
  return view ? { view, apiPath: `/api/team/${game}/${team}${q}`,
                  pagePath: `/g/${game}/team/${team}`, pin, sp } : null;
}

/** What this page was asked, for a signed-in owner's recap. The backend checks it again. */
function asked(view: TeamView, sp: Record<string, string | string[] | undefined>,
               pin?: string): Remembered {
  const one = (v: string | string[] | undefined) => (typeof v === "string" && v ? v : null);
  const playId = one(sp.play_id);
  return {
    kind: "team", game_id: view.game.id, target: view.team.espn_id, play: one(sp.play),
    market: one(sp.market) ?? pin ?? null,
    play_id: playId && /^\d{1,9}$/.test(playId) ? Number(playId) : null,
  };
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
  const owner = await currentOwner();
  const { view } = data;
  return (
    <>
      <ResultView initial={view} apiPath={data.apiPath} pagePath={data.pagePath} pin={data.pin}
                  aside={owner ? <MyHistory kind="team" id={view.team.espn_id}
                                            gameId={view.game.id} name={view.team.name} />
                               : undefined} />
      {owner && <RememberLookup body={asked(view, data.sp, data.pin)} />}
    </>
  );
}
