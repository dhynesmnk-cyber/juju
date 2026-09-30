export const metadata = { title: "How Juju works" };

export default function About() {
  return (
    <div className="prose">
      <h1>How Juju works</h1>
      <p>
        Juju answers one question about a play you just saw: <strong>what would a $10 bet,
        placed 45 minutes before kickoff, have paid?</strong>
      </p>
      <h2>The prices are real</h2>
      <p>
        Before every game, Juju saves the sportsbooks&apos; prices several times, ending at 45
        minutes before kickoff. A card shows the price from the latest save at or before that
        moment, with its time. If the only price we have is earlier, the card says so. We never
        estimate a price between two saves, and we never use one from after T-45.
      </p>
      <p>
        We show one book first: Hard Rock Bet, then DraftKings, then others, whichever had the
        price on time. Other books&apos; prices are listed underneath. We never pick the best
        price for the headline.
      </p>
      <h2>Live status catches up</h2>
      <p>
        The official feed usually runs 10 to 60 seconds behind TV. You&apos;ll often see the price
        before the play reaches the feed. The page updates itself, and a bet whose line is
        already beaten shows as <em>cashed, if the play stands</em>. It becomes <em>won</em> 10
        minutes after the final whistle.
      </p>
      <h2>Fair value</h2>
      <p>
        When the book offered both sides of a bet, we also show what $10 would have paid with
        the book&apos;s margin taken out. Some bets, like anytime touchdown scorers, only have a
        &quot;yes&quot; side, so there is no fair value to show.
      </p>
      <h2>Same-game parlays</h2>
      <p>
        When we add parlays, they&apos;ll use standard parlay maths. Books adjust same-game
        parlays for correlation, so a real ticket would have paid less, and every parlay card
        will say so.
      </p>
      <h2>What Juju is not</h2>
      <p>
        Juju is not a sportsbook. It takes no bets, holds no money and doesn&apos;t recommend
        bets. Every payout it shows is hypothetical.
      </p>
    </div>
  );
}
