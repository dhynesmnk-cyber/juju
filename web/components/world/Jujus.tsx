// The Jujus: Pip, Bo and Tuft, three small original characters who sit on the first card and
// react to the bet's world. Drawn as SVG (crisp, tiny) and moved by CSS (globals.css), so they
// cost nothing when still and stop moving under reduced motion. Purely decorative: aria-hidden.
import type { Mood } from "./worlds";

type Eyes = "open" | "sleepy" | "joy" | "sad";
type Mouth = "rest" | "open" | "big" | "o" | "sad";
const INK = "#141824";

const EYES: Record<Eyes, (x: number, y: number, s: number) => string> = {
  open: (x, y, s) => `<g class="eyes"><ellipse cx="${x - s}" cy="${y}" rx="3.2" ry="4" fill="#fff"/><ellipse cx="${x + s}" cy="${y}" rx="3.2" ry="4" fill="#fff"/><g class="pupils"><circle cx="${x - s + 0.6}" cy="${y + 0.6}" r="2" fill="${INK}"/><circle cx="${x + s + 0.6}" cy="${y + 0.6}" r="2" fill="${INK}"/></g></g>`,
  sleepy: (x, y, s) => `<g stroke="${INK}" stroke-width="1.8" stroke-linecap="round" fill="none"><path d="M${x - s - 3} ${y + 1} q3 2 6 0"/><path d="M${x + s - 3} ${y + 1} q3 2 6 0"/></g>`,
  joy: (x, y, s) => `<g stroke="${INK}" stroke-width="2" stroke-linecap="round" fill="none"><path d="M${x - s - 3} ${y + 1} q3 -4 6 0"/><path d="M${x + s - 3} ${y + 1} q3 -4 6 0"/></g>`,
  sad: (x, y, s) => `<g><path d="M${x - s - 4} ${y - 4} l6 -2.5" stroke="${INK}" stroke-width="1.6" stroke-linecap="round"/><path d="M${x + s + 4} ${y - 4} l-6 -2.5" stroke="${INK}" stroke-width="1.6" stroke-linecap="round"/><ellipse cx="${x - s}" cy="${y + 1}" rx="2.6" ry="3" fill="#fff"/><ellipse cx="${x + s}" cy="${y + 1}" rx="2.6" ry="3" fill="#fff"/><circle cx="${x - s}" cy="${y + 2}" r="1.7" fill="${INK}"/><circle cx="${x + s}" cy="${y + 2}" r="1.7" fill="${INK}"/></g>`,
};
const MOUTH: Record<Mouth, (x: number, y: number) => string> = {
  rest: (x, y) => `<path d="M${x - 3} ${y} q3 2.5 6 0" stroke="${INK}" stroke-width="1.8" fill="none" stroke-linecap="round"/>`,
  open: (x, y) => `<ellipse cx="${x}" cy="${y + 1}" rx="3.4" ry="3" fill="${INK}"/><ellipse cx="${x}" cy="${y + 2.4}" rx="2" ry="1.2" fill="#ff7a8a"/>`,
  big: (x, y) => `<path d="M${x - 6} ${y - 1} q6 9 12 0 z" fill="${INK}"/><path d="M${x - 3} ${y + 3} q3 2 6 0" fill="#ff7a8a"/>`,
  o: (x, y) => `<ellipse cx="${x}" cy="${y + 1}" rx="2" ry="2.4" fill="${INK}"/>`,
  sad: (x, y) => `<path d="M${x - 3} ${y + 2} q3 -2.5 6 0" stroke="${INK}" stroke-width="1.8" fill="none" stroke-linecap="round"/>`,
};
const FACE: Record<Mood, [Eyes, Mouth]> = {
  sleepy: ["sleepy", "rest"], cheer: ["open", "open"], look: ["open", "o"], joy: ["joy", "big"],
  content: ["joy", "rest"], sad: ["sad", "sad"], calm: ["open", "rest"], fix: ["open", "o"],
};

const cheeks = (a: number, b: number, y: number) =>
  `<ellipse cx="${a}" cy="${y}" rx="3" ry="1.8" fill="#ff8fa3" opacity=".55"/><ellipse cx="${b}" cy="${y}" rx="3" ry="1.8" fill="#ff8fa3" opacity=".55"/>`;
const feet = (c: string, a: number, b: number) =>
  `<ellipse cx="${a}" cy="67" rx="6" ry="3" fill="${c}"/><ellipse cx="${b}" cy="67" rx="6" ry="3" fill="${c}"/>`;

export function jujuSvg(kind: "pip" | "bo" | "tuft", mood: Mood): string {
  const [e, m] = FACE[mood];
  const E = EYES[e], Mo = MOUTH[m];
  if (kind === "pip") return `${feet("#e0962a", 22, 38)}
    <path d="M30 14 q-2 -8 4 -10" stroke="#e0962a" stroke-width="2.4" fill="none" stroke-linecap="round"/><circle cx="34.5" cy="4" r="3" fill="#ffd27a"/>
    <circle cx="30" cy="42" r="24" fill="#ffb43d"/><ellipse cx="22" cy="30" rx="7" ry="4" fill="#fff" opacity=".25"/>
    ${E(30, 38, 7)}${cheeks(19, 41, 46)}${Mo(30, 49)}
    <g class="prop confetti"><rect x="50" y="10" width="5" height="3" fill="#fff" transform="rotate(30 52 11)"/><rect x="56" y="22" width="5" height="3" fill="#6fe3b4" transform="rotate(-20 58 23)"/><rect x="4" y="12" width="5" height="3" fill="#b79cff" transform="rotate(15 6 13)"/><rect x="46" y="0" width="4" height="3" fill="#ffc53d"/></g>
    <g class="prop wrench"><path d="M52 30 l8 -8" stroke="#9aa3b7" stroke-width="3" stroke-linecap="round"/><circle cx="61" cy="21" r="3.2" fill="none" stroke="#9aa3b7" stroke-width="2"/></g>
    <g class="prop trophy"><path d="M50 50 h10 v4 q0 5 -5 5 q-5 0 -5 -5z" fill="#ffc53d"/><rect x="53.5" y="59" width="3" height="4" fill="#ffc53d"/><rect x="51" y="63" width="8" height="2.5" rx="1" fill="#c9941e"/></g>`;
  if (kind === "bo") return `${feet("#49b98f", 16, 30)}
    <rect x="6" y="6" width="32" height="58" rx="16" fill="#6fe3b4"/><ellipse cx="15" cy="18" rx="5" ry="7" fill="#fff" opacity=".25"/>
    ${E(22, 24, 6)}${cheeks(12, 32, 32)}${Mo(22, 35)}
    <g class="prop binos"><circle cx="16" cy="24" r="5.2" fill="#2b3346"/><circle cx="28" cy="24" r="5.2" fill="#2b3346"/><rect x="19" y="22" width="6" height="4" fill="#2b3346"/><circle cx="16" cy="24" r="2.6" fill="#8fd3ff"/><circle cx="28" cy="24" r="2.6" fill="#8fd3ff"/></g>
    <g class="prop umbrella"><path d="M-8 4 q30 -22 60 0 z" fill="#5f6b86"/><path d="M22 4 v34" stroke="#9aa3b7" stroke-width="2"/></g>`;
  return `${feet("#8f74e0", 20, 36)}
    <path d="M18 20 q-4 -14 6 -12 q2 -8 8 -2 q8 -6 8 6 q8 -2 4 10 z" fill="#c9b5ff"/>
    <ellipse cx="28" cy="42" rx="24" ry="22" fill="#b79cff"/><ellipse cx="20" cy="32" rx="7" ry="4" fill="#fff" opacity=".25"/>
    ${E(28, 40, 7)}${cheeks(17, 39, 48)}${Mo(28, 51)}
    <g class="prop pennant"><path d="M50 18 v34" stroke="#d8def0" stroke-width="2"/><path d="M50 18 l16 5 l-16 5 z" fill="#ffc53d"/><text x="52" y="26" font-size="6" font-weight="700" fill="#1a1200">J</text></g>`;
}

// Which prop each mood shows (globals.css does the same with opacity).
const PROP: Partial<Record<Mood, string>> = {
  cheer: "pennant", look: "binos", joy: "confetti", content: "trophy", sad: "umbrella",
  fix: "wrench",
};
const VIEWBOX = { pip: "0 0 60 70", bo: "0 0 44 70", tuft: "0 0 60 70" } as const;

/** A standalone, still SVG document for places with no CSS (the share image): only the
 *  mood's own prop is kept. Props hold no nested groups, so a lazy match is exact. */
export function stillJujuSvg(kind: "pip" | "bo" | "tuft", mood: Mood): string {
  const inner = jujuSvg(kind, mood).replace(
    /<g class="prop (\w+)">[\s\S]*?<\/g>/g, (group, name) => (name === PROP[mood] ? group : ""));
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${VIEWBOX[kind]}">${inner}</svg>`;
}

export default function Jujus({ mood }: { mood: Mood }) {
  // The markup is built from the constants above only, never from data.
  return (
    <div className={`crew mood-${mood}`} aria-hidden="true" data-mood={mood}>
      <div className="j pip"><svg viewBox="0 0 60 70" dangerouslySetInnerHTML={{ __html: jujuSvg("pip", mood) }} /></div>
      <div className="j bo"><svg viewBox="0 0 44 70" dangerouslySetInnerHTML={{ __html: jujuSvg("bo", mood) }} /></div>
      <div className="j tuft"><svg viewBox="0 0 60 70" dangerouslySetInnerHTML={{ __html: jujuSvg("tuft", mood) }} /></div>
      <span className="bubble z">z z</span>
      <span className="bubble q">?</span>
    </div>
  );
}
