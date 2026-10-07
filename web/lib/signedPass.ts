// A cookie value that says only when it expires, "<expires>.<mac>", signed with a key derived
// from a secret and a label (each cookie its own label). Server-side only. No "@/" imports:
// `npm test` runs the lib tests with Node's own test runner.
import { createHmac, timingSafeEqual } from "node:crypto";

function macOf(label: string, secret: string, expires: string): Buffer {
  const key = createHmac("sha256", label).update(secret).digest();
  return createHmac("sha256", key).update(expires).digest();
}

export function signPass(label: string, secret: string, nowMs: number, seconds: number): string {
  const expires = String(Math.floor(nowMs / 1000) + seconds);
  return `${expires}.${macOf(label, secret, expires).toString("base64url")}`;
}

export function passIsValid(label: string, secret: string, value: string | undefined,
                            nowMs: number): boolean {
  const [expires, mac, extra] = (value ?? "").split(".");
  if (!expires || !mac || extra !== undefined || !/^\d{1,12}$/.test(expires)) return false;
  if (Number(expires) * 1000 <= nowMs) return false;
  const want = macOf(label, secret, expires);
  const got = Buffer.from(mac, "base64url");
  return got.length === want.length && timingSafeEqual(got, want);
}
