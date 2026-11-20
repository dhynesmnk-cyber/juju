import ArchiveHome from "@/components/ArchiveHome";
import LiveHome from "@/components/LiveHome";
import { liveChatOpen } from "@/lib/publicApi";

export const dynamic = "force-dynamic";

// With a passcode on the site, the front page is the live chat (docs/plans/live-chat.md) and the
// T-45 archive is at /archive. Without one the live chat is off (it spends credits on every
// question, with no per-visitor cap), and this is the archive's front page as it always was.
export default async function Home() {
  return liveChatOpen(process.env.SITE_PASSCODE) ? <LiveHome /> : <ArchiveHome />;
}
