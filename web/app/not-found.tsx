import Link from "next/link";

export default function NotFound() {
  return (
    <div className="prose">
      <h1>Nothing here</h1>
      <p>We couldn&apos;t find that game or player. <Link href="/">Look up a play</Link>.</p>
    </div>
  );
}
