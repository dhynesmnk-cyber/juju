export const metadata = { title: "Privacy" };

export default function Privacy() {
  return (
    <div className="prose">
      <h1>Privacy</h1>
      <p>Juju has no accounts and asks for no personal details.</p>
      <p>We keep the text of lookups, without any address or device identifier, to improve how
        Juju understands plays. Your IP address is used only for a few minutes to limit abuse,
        and is not stored.</p>
      <p>Your 21+ confirmation is kept in your own browser.</p>
      <p>If you look up a lot of plays in a short time, we may ask you to pass a quick check
        from Cloudflare Turnstile, which runs in Cloudflare&apos;s own frame under
        Cloudflare&apos;s privacy policy. Once you pass, a cookie that holds only its expiry
        time spares you the check for an hour.</p>
    </div>
  );
}
