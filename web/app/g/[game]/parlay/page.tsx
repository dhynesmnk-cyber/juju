import type { Metadata } from "next";
import { notFound } from "next/navigation";
import ParlayView from "@/components/ParlayView";
import { backendGet } from "@/lib/backend";
import type { ParlayView as View } from "@/lib/types";

export const dynamic = "force-dynamic";

type Props = {
  params: Promise<{ game: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

const LEGS = /^[a-z0-9_.,-]{1,600}$/;

async function load(props: Props) {
  const { game } = await props.params;
  const { legs } = await props.searchParams;
  if (!/^\d+$/.test(game) || typeof legs !== "string" || !LEGS.test(legs)) return null;
  const apiPath = `/api/parlay/${game}?legs=${encodeURIComponent(legs)}`;
  const view = await backendGet<View>(apiPath);
  return view ? { view, apiPath } : null;
}

export async function generateMetadata(props: Props): Promise<Metadata> {
  const data = await load(props);
  if (!data) return { title: "Juju" };
  return {
    title: `Same-game parlay, ${data.view.game.label} | Juju`,
    description: `${data.view.legs.length} legs: ${data.view.headline}. Standard parlay maths `
      + "at prices archived 45 minutes before kickoff.",
  };
}

export default async function ParlayPage(props: Props) {
  const data = await load(props);
  if (!data) notFound();
  return <ParlayView initial={data.view} apiPath={data.apiPath} />;
}
