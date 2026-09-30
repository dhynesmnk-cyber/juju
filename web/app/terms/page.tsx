// Draft terms: have a lawyer review them before public launch (docs/GOALS.md section 11).
export const metadata = { title: "Terms of use" };

export default function Terms() {
  return (
    <div className="prose">
      <h1>Terms of use</h1>
      <p>Juju is an information tool for adults 21 and older. It is not a sportsbook: it takes no
        bets, holds no funds and makes no recommendations. Payouts shown are hypothetical.</p>
      <p>Prices come from third-party data archived before kickoff and may contain errors. Stats
        can be corrected after a game. Don&apos;t rely on Juju for any financial decision.</p>
      <p>You may use Juju for your own, personal purposes. You may not scrape, copy or
        redistribute its data or build a service on top of it, automatically or otherwise.</p>
    </div>
  );
}
