import type { Metadata, Viewport } from "next";
import { Big_Shoulders } from "next/font/google";
import { cookies } from "next/headers";
import Link from "next/link";
import AgeGate from "@/components/AgeGate";
import StatusBanner from "@/components/StatusBanner";
import { backendGet } from "@/lib/backend";
import { currentOwner, trackerOn } from "@/lib/me";
import { type StatusBody, statusMessage } from "@/lib/status";
import { color, cssVariables } from "@/lib/tokens";
import "./globals.css";

const display = Big_Shoulders({
  // "optional": the font is preloaded and used if it is there for the first paint; it never
  // swaps in afterwards, so a heading never re-wraps under the finger.
  subsets: ["latin"], weight: ["800", "900"], variable: "--font-display", display: "optional",
});

export const metadata: Metadata = {
  // Share previews need absolute image URLs: SITE_URL is the public address (docs/deploy.md).
  metadataBase: new URL(process.env.SITE_URL ?? "http://localhost:3000"),
  title: "Juju: what would $10 have paid?",
  description:
    "Tap or type a recent NFL play and see what a $10 bet placed 45 minutes before kickoff " +
    "would have paid, at real archived prices. Not a sportsbook.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: color.bg,
  colorScheme: "dark",
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const confirmed = (await cookies()).get("juju21")?.value === "yes";
  const status = statusMessage(await backendGet<StatusBody>("/api/status", 1500));
  const owner = await currentOwner();
  return (
    <html lang="en" className={display.variable}>
      <head>
        {/* Every colour the stylesheet reads: lib/tokens.ts, the one place they are named. */}
        <style id="tokens" dangerouslySetInnerHTML={{ __html: cssVariables() }} />
      </head>
      <body>
        <div className="page">
          <header className="top">
            <div className="wrap bar">
              <Link href="/" className="brand">ju<span>ju</span></Link>
              <nav aria-label="Site">
                {owner && <Link href="/me" prefetch={false}>Your lookups</Link>}
                <Link href="/about">How it works</Link>
              </nav>
            </div>
          </header>
          <div className="wrap">
            <StatusBanner initial={status} />
            <main>{children}</main>
            <footer className="foot">
              <p>
                Juju is not a sportsbook. It takes no bets and every payout shown is
                hypothetical: what $10 would have returned at a real price archived before
                kickoff.
              </p>
              <p>
                21+ only. Gambling problem? Call <a href="tel:18004262537">1-800-GAMBLER</a>.{" "}
                <Link href="/responsible-gambling">Responsible gambling</Link>
              </p>
              <p>
                Odds data: The Odds API. Play-by-play verification: nflverse (CC BY 4.0).{" "}
                <Link href="/terms">Terms</Link> · <Link href="/privacy">Privacy</Link>
                {/* A plain link: the sign-in form is HTML from proxy.ts, not an app page. */}
                {trackerOn() && !owner && <> · <a href="/me">Owners</a></>}
              </p>
            </footer>
          </div>
        </div>
        <AgeGate confirmed={confirmed} />
      </body>
    </html>
  );
}
