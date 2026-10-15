/** Shown at once when a play, a name or a link is tapped, while the server gets the cards. */
export default function Loading() {
  return (
    <div className="loading" role="status" aria-label="Loading the cards">
      <div className="sk sk-title" />
      <div className="sk sk-line" />
      <div className="sk sk-card" />
      <div className="sk sk-card" />
    </div>
  );
}
