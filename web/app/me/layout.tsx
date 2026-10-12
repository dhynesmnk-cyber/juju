import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { trackerEnabled } from "@/lib/trackerSession";

// Private: never indexed, never cached, never in a share preview.
export const metadata: Metadata = {
  title: "Watch my parlays",
  robots: { index: false, follow: false },
};
export const dynamic = "force-dynamic";

/** Without TRACKER_USERS there is no /me: the site's own 404, as for any other address. */
export default function TrackerLayout({ children }: { children: React.ReactNode }) {
  if (!trackerEnabled()) notFound();
  return children;
}
