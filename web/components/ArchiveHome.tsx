import LiveNow from "@/components/LiveNow";
import MyRecaps from "@/components/MyRecaps";
import SearchBox from "@/components/SearchBox";
import { backendGet } from "@/lib/backend";
import { currentOwner } from "@/lib/me";
import type { LiveResponse } from "@/lib/types";

/** The T-45 archive's front page: what a $10 bet 45 minutes before kickoff would have paid.
 *  It is `/archive`, and also `/` wherever the live chat is off (no passcode on the site). */
export default async function ArchiveHome() {
  const live = await backendGet<LiveResponse>("/api/live");
  const owner = await currentOwner();
  return (
    <>
      <h1 className="home-title">What would that play have paid?</h1>
      <p className="tagline">
        Tap a play or type one. See what a <strong>$10 bet placed 45 minutes before
        kickoff</strong> would have paid, at the real price.
      </p>
      <SearchBox />
      {owner && <MyRecaps />}
      <LiveNow initial={live} />
    </>
  );
}
