export function american(n: number): string {
  return n > 0 ? `+${n}` : `${n}`;
}

const timeFmt = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit" });
const etFmt = new Intl.DateTimeFormat("en-US", {
  hour: "numeric", minute: "2-digit", timeZone: "America/New_York", timeZoneName: "short",
});
const dayFmt = new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric" });

/** Local time, with ET alongside when the viewer isn't on Eastern time. */
export function localAndEt(iso: string): string {
  const d = new Date(iso);
  const local = timeFmt.format(d);
  const et = etFmt.format(d);
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return tz === "America/New_York" ? et : `${local} (${et})`;
}

export function dayAndTime(iso: string): string {
  const d = new Date(iso);
  return `${dayFmt.format(d)}, ${localAndEt(iso)}`;
}

export function secondsAgo(s: number | null): string {
  if (s === null) return "not yet updated";
  if (s < 60) return `updated ${s}s ago`;
  return `updated ${Math.floor(s / 60)} min ago`;
}
