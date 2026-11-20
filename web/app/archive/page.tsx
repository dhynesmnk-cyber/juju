import type { Metadata } from "next";
import ArchiveHome from "@/components/ArchiveHome";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "The archive | Juju" };

export default async function Archive() {
  return <ArchiveHome />;
}
