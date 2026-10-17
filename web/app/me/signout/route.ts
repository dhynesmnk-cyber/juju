import { NextResponse } from "next/server";
import { TRACKER_COOKIE } from "@/lib/trackerPass";

/** Sign this phone out of the owners' section. A POST, so no link prefetch can do it. */
export async function POST() {
  const res = new NextResponse(null, {
    status: 303, headers: { location: "/", "cache-control": "no-store" },
  });
  res.cookies.set(TRACKER_COOKIE, "", {
    httpOnly: true, secure: true, sameSite: "lax", path: "/", maxAge: 0,
  });
  return res;
}
