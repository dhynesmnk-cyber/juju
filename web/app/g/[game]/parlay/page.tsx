import type { Metadata } from "next";
import { notFound } from "next/navigation";
import ParlayView from "@/components/ParlayView";
import { payoutText } from "@/components/Payout";
import RememberLookup from "@/components/RememberLookup";
import { backendPage } from "@/lib/backend";
import { currentOwner } from "@/lib/me";
import { LEGS, SIZE } from "@/lib/shareImage";
import type { ParlayView as View } from "@/lib/types";

export const dynamic = "force-dynamic";

type Props = {
  params: Promise<{ game: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};



async function load(props: Props) {
  const { game } = await props.params;
  const { legs } = await props.searchParams;
  if (!/^\d+$/.test(game) || typeof legs !== "string" || !LEGS.test(legs)) return null;
  const apiPath = `/api/parlay/${game}?legs=${encodeURIComponent(legs)}`;
  const view = await backendPage<View>(apiPath);
  return view ? { view, apiPath, legs } : null;
}

function pagePath(game: number, legs: string): string {
  return `/g/${game}/parlay?legs=${encodeURIComponent(legs)}`;
}

export async function generateMetadata(props: Props): Promise<Metadata> {
  const data = await load(props);
  if (!data) return { title: "Juju" };
  const { view, legs } = data;
  const title = `Same-game parlay, ${view.game.label} | Juju`;
  const description = `${view.legs.length} legs: ${payoutText(view)}. Standard parlay maths `
    + "at prices archived 45 minutes before kickoff; a real ticket would have paid less.";
  const image = { url: `/g/${view.game.id}/parlay/image?legs=${encodeURIComponent(legs)}`,
                  ...SIZE, alt: `${title}: ${payoutText(view)}` };
  return { title, description, openGraph: { title, description, images: [image] },
           twitter: { card: "summary_large_image", title, description, images: [image] } };
}

export default async function ParlayPage(props: Props) {
  const data = await load(props);
  if (!data) notFound();
  const owner = await currentOwner();
  return (
    <>
      <ParlayView initial={data.view} apiPath={data.apiPath}
                  sharePath={pagePath(data.view.game.id, data.legs)} />
      {owner && <RememberLookup body={{ kind: "parlay", game_id: data.view.game.id,
                                        target: data.legs }} />}
    </>
  );
}
