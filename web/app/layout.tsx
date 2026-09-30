import type { Metadata, Viewport } from "next";
import Link from "next/link";
import AgeGate from "@/components/AgeGate";
import StatusBanner from "@/components/StatusBanner";
import "./globals.css";

export const metadata: Metadata = {
  title: "Juju: what would $10 have paid?",
  description:
    "Tap or type a recent NFL play and see what a $10 bet placed 45 minutes before kickoff " +
    "would have paid, at real archived prices. Not a sportsbook.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f6f7f9" },
    { media: "(prefers-color-scheme: dark)", color: "#0d1015" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="wrap">
          <header className="top">
            <Link href="/" className="brand">ju<span>ju</span></Link>
            <nav aria-label="About Juju">
              <Link href="/about">How it works</Link>
            </nav>
          </header>
          <StatusBanner />
          <main>{children}</main>
          <footer className="foot">
            <p>
              Juju is not a sportsbook. It takes no bets and every payout shown is hypothetical:
              what $10 would have returned at a real price archived before kickoff.
            </p>
            <p>
              21+ only. Gambling problem? Call <a href="tel:18004262537">1-800-GAMBLER</a>.{" "}
              <Link href="/responsible-gambling">Responsible gambling</Link>
            </p>
            <p>
              Odds data: The Odds API. Play-by-play verification: nflverse (CC BY 4.0).{" "}
              <Link href="/terms">Terms</Link> · <Link href="/privacy">Privacy</Link>
            </p>
          </footer>
        </div>
        <AgeGate />
      </body>
    </html>
  );
}
