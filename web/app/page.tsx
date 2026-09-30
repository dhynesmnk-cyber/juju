import LiveNow from "@/components/LiveNow";
import SearchBox from "@/components/SearchBox";
import { backendGet } from "@/lib/backend";
import type { LiveResponse } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function Home() {
  const live = await backendGet<LiveResponse>("/api/live");
  return (
    <>
      <p className="tagline">
        Tap a play or type one. See what a <strong>$10 bet placed 45 minutes before
        kickoff</strong> would have paid, at the real price.
      </p>
      <SearchBox />
      <LiveNow initial={live} />
    </>
  );
}
