"use client";

import { useEffect } from "react";
import { forgetEndedThreads } from "@/lib/liveChat";

/** Drops the saved chat of any game that has left the live schedule (it is over). It draws
 *  nothing, and does nothing unless the schedule loaded with games in it. */
export default function ThreadTidy({ onSchedule }: { onSchedule: string[] }) {
  useEffect(() => {
    if (onSchedule.length === 0) return;
    try {
      forgetEndedThreads(window.localStorage, onSchedule);
    } catch {
      // storage blocked: nothing saved, nothing to tidy
    }
  }, [onSchedule]);
  return null;
}
