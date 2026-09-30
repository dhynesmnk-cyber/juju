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
    </div>
  );
}
