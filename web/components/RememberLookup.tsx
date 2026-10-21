"use client";

import { useEffect, useRef } from "react";
import { remember } from "@/lib/remember";
import type { Remembered } from "@/lib/types";

/** Saves the bet page it is on for the signed-in owner, once it is on screen: never for a
 *  prefetch, a share image or anyone signed out (the page renders this only for an owner). */
export default function RememberLookup({ body }: { body: Remembered }) {
  const key = JSON.stringify(body);
  const sent = useRef<string | null>(null);
  useEffect(() => {
    if (sent.current === key) return;  // once per page, even when effects run twice
    sent.current = key;
    remember(JSON.parse(key));
  }, [key]);
  return null;
}
