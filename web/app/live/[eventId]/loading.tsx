/** Shown at once when a game is opened, while the page finds it on the live schedule. */
export default function Loading() {
  return (
    <div className="loading" role="status" aria-label="Loading the game">
      <div className="sk sk-title" />
      <div className="sk sk-line" />
      <div className="sk sk-row" />
    </div>
  );
}
