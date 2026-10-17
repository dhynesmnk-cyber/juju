import { notFound } from "next/navigation";
import RecapView from "@/components/RecapView";
import { currentOwner, meGet } from "@/lib/me";
import type { Recap } from "@/lib/types";

export const dynamic = "force-dynamic";

type Props = { params: Promise<{ game: string }> };

/** One game's recap, for the owner signed in. */
export default async function RecapPage(props: Props) {
  const { game } = await props.params;
  const owner = await currentOwner();
  if (!owner || !/^\d+$/.test(game)) notFound();
  const recap = await meGet<Recap>(`recaps/${game}`, owner);
  if (!recap) notFound();
  return <RecapView initial={recap} />;
}
