export function american(n: number): string {
  return n > 0 ? `+${n}` : `${n}`;
}

const ET = "America/New_York";
const etFmt = new Intl.DateTimeFormat("en-US", {
  hour: "numeric", minute: "2-digit", timeZone: ET, timeZoneName: "short",
});
const etDayFmt = new Intl.DateTimeFormat("en-US", {
  weekday: "short", month: "short", day: "numeric", timeZone: ET,
});

/** Times are the same text on the server and in the browser: Eastern time until the page knows
 *  the viewer's zone (`tz`, from `LocalTime` after hydration), then local with ET beside it.
 *  (Formatting in the runtime's own zone made the server, on UTC, and a phone disagree, and
 *  React redrew the page.) */
export function localAndEt(iso: string, tz: string | null = null): string {
  const d = new Date(iso);
  const et = etFmt.format(d);
  if (!tz || tz === ET) return et;
  const local = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", timeZone: tz })
    .format(d);
  return `${local} (${et})`;
}

export function dayAndTime(iso: string, tz: string | null = null): string {
  const d = new Date(iso);
  const day = !tz || tz === ET ? etDayFmt.format(d) : new Intl.DateTimeFormat("en-US", {
    weekday: "short", month: "short", day: "numeric", timeZone: tz,
  }).format(d);
  return `${day}, ${localAndEt(iso, tz)}`;
}

const etLongDayFmt = new Intl.DateTimeFormat("en-US", {
  weekday: "long", month: "short", day: "numeric", timeZone: ET,
});

/** "Sunday, Oct 18": the NFL's day for a kickoff, always in Eastern time, so the server and every
 *  phone print the same heading (a Sunday-night game is already Monday in UTC). */
export function etDay(iso: string): string {
  return etLongDayFmt.format(new Date(iso));
}

/** Runs of consecutive items on the same Eastern day, in the order given. */
export function byEtDay<T extends { commence_time: string }>(items: T[]): [string, T[]][] {
  const out: [string, T[]][] = [];
  for (const item of items) {
    const day = etDay(item.commence_time);
    const last = out[out.length - 1];
    if (last && last[0] === day) last[1].push(item);
    else out.push([day, [item]]);
  }
  return out;
}

export function secondsAgo(s: number | null): string {
  if (s === null) return "not yet updated";
  if (s < 60) return `updated ${s}s ago`;
  return `updated ${Math.floor(s / 60)} min ago`;
}

/** "Q3", or "OT" (and "2OT") after the fourth quarter. */
export function quarter(period: number | null): string {
  if (period === null) return "";
  if (period <= 4) return `Q${period}`;
  return period === 5 ? "OT" : `${period - 4}OT`;
}
