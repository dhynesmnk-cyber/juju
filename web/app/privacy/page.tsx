export const metadata = { title: "Privacy" };

export default function Privacy() {
  return (
    <div className="prose">
      <h1>Privacy</h1>
      <p>Juju has no public accounts and asks for no personal details.</p>
      <p>We keep the text of lookups, without any address or device identifier, to improve how
        Juju understands plays. Your IP address is used only for a few minutes to limit abuse,
        and is not stored.</p>
      <p>Your 21+ confirmation is kept in your own browser.</p>
      <p>If you look up a lot of plays in a short time, we may ask you to pass a quick check
        from Cloudflare Turnstile, which runs in Cloudflare&apos;s own frame under
        Cloudflare&apos;s privacy policy. Once you pass, a cookie that holds only its expiry
        time spares you the check for an hour.</p>
      <p>Juju&apos;s owners can sign in to a private section of their own. While one of them is
        signed in, the bet pages they open are kept under their name, so they can look back on
        how those bets went. Nobody else&apos;s lookups are kept this way, and nobody else can
        see theirs.</p>
      <p>The live odds chat is open only to people who have the site&apos;s passcode. Your
        questions and the last few messages of the chat are used to look up live prices and are
        not stored on Juju&apos;s servers; the chat itself is kept in your own browser. When the
        chat&apos;s assistant is switched on, a question Juju can&apos;t read by itself, and
        those last few messages, are sent to DeepSeek, an outside AI service, to work out what
        you meant and to word the reply. Don&apos;t put personal details in them. The prices and
        numbers in a reply always come from the books&apos; live feed, never from the
        assistant.</p>
    </div>
  );
}
