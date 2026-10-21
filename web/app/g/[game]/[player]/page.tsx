import type { Metadata } from "next";
import { notFound } from "next/navigation";
import MyHistory from "@/components/MyHistory";
import RememberLookup from "@/components/RememberLookup";
import ResultView from "@/components/ResultView";
import { backendPage } from "@/lib/backend";
import { currentOwner } from "@/lib/me";
import { CARD_KEY, SIZE, imagePath, pickCard } from "@/lib/shareImage";
import type { PlayerView, Remembered } from "@/lib/types";

export const dynamic = "force-dynamic";

type Props = {
  params: Promise<{ game: string; player: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

const KEEP = ["play", "market", "threshold", "yards", "expect", "play_id"];

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
  const view = await backendPage<PlayerView>(`/api/player/${game}/${player}${q}`);
  const pin = typeof sp.card === "string" && CARD_KEY.test(sp.card) ? sp.card : undefined;
  return view ? { view, apiPath: `/api/player/${game}/${player}${q}`,
                  pagePath: `/g/${game}/${player}`, pin, sp } : null;
}

function one(v: string | string[] | undefined): string | null {
  return typeof v === "string" && v ? v : null;
}

/** What this page was asked, for a signed-in owner's recap (a shared `?card=` counts as the
 *  market). The backend checks it all again. */
function asked(view: PlayerView, sp: Record<string, string | string[] | undefined>,
               pin?: string): Remembered {
  const num = (v: string | null) => (v && /^\d{1,9}$/.test(v) ? Number(v) : null);
  return {
    kind: "player", game_id: view.game.id, target: view.player.id, play: one(sp.play),
    market: one(sp.market) ?? pin ?? null, threshold: one(sp.threshold),
    yards: num(one(sp.yards)), expect: !!one(sp.expect), play_id: num(one(sp.play_id)),
  };
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
  const owner = await currentOwner();
  const { view } = data;
  return (
    <>
      <ResultView initial={view} apiPath={data.apiPath} pagePath={data.pagePath} pin={data.pin}
                  aside={owner ? <MyHistory kind="player" id={view.player.id}
                                            gameId={view.game.id} name={view.player.name} />
                               : undefined} />
      {owner && <RememberLookup body={asked(view, data.sp, data.pin)} />}
    </>
  );
}
