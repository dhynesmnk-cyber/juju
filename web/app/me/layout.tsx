import type { Metadata } from "next";
import { currentOwner } from "@/lib/me";
import { ME_SIGNOUT_PATH } from "@/lib/trackerPass";

// The owners' own section: proxy.ts lets only a signed-in owner this far.
export const metadata: Metadata = {
  title: "Your lookups | Juju",
  robots: { index: false, follow: false },
};

export default async function MeLayout({ children }: { children: React.ReactNode }) {
  const owner = await currentOwner();
  return (
    <div className="me">
      {owner && (
        <div className="me-bar">
          <span className="muted small">Signed in as <strong>{owner}</strong>. Only you see this.</span>
          <form method="post" action={ME_SIGNOUT_PATH}>
            <button type="submit" className="link-button">Sign out</button>
          </form>
        </div>
      )}
      {children}
    </div>
  );
}
