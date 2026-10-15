"use client";

import { useSyncExternalStore } from "react";
import { dayAndTime, localAndEt } from "@/lib/format";

const noop = () => () => {};
const zone = () => Intl.DateTimeFormat().resolvedOptions().timeZone;

/** A time in the viewer's zone with ET beside it. The server and the first render in the
 *  browser both show ET, so they agree; the local time follows straight after. */
export default function LocalTime({ iso, withDay = false }: { iso: string; withDay?: boolean }) {
  const tz = useSyncExternalStore(noop, zone, () => null);
  return <time dateTime={iso}>{withDay ? dayAndTime(iso, tz) : localAndEt(iso, tz)}</time>;
}
