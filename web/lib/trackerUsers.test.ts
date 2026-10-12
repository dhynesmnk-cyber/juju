// `npm test`: Node's own test runner, no network.
import assert from "node:assert/strict";
import { test } from "node:test";
import { isPublicPath } from "./publicApi.ts";
import {
  TRACKER_SECONDS, isTrackerPath, parseTrackerUsers, signIn, trackerGate, trackerNext,
  trackerPass, trackerUserFrom,
} from "./trackerUsers.ts";
import { trackerUnlockHtml } from "./unlockPage.ts";

const NOW = 1_790_000_000_000;
const RAW = "Chris:lantern-quartz-5512, Ash:harbor-violet-3307";
const { users: USERS } = parseTrackerUsers(RAW);
const [CHRIS, ASH] = USERS;

test("TRACKER_USERS gives each user a name and a passcode", () => {
  assert.deepEqual(USERS, [{ name: "Chris", passcode: "lantern-quartz-5512" },
                           { name: "Ash", passcode: "harbor-violet-3307" }]);
  assert.deepEqual(parseTrackerUsers(undefined), { users: [], problems: [] });
  assert.deepEqual(parseTrackerUsers(" , ").users, []);
  // Only the first colon splits: a passcode may have its own.
  assert.equal(parseTrackerUsers("Ash:a:b:c:d:e:f:g").users[0].passcode, "a:b:c:d:e:f:g");
});

test("a short passcode, a bad name or a name used twice leaves that user out", () => {
  const { users, problems } = parseTrackerUsers(
    "Chris:nine-char,Ash:harbor-violet-3307,ash:another-long-one,Pat Lee:long-enough-pass,Sam");
  assert.deepEqual(users.map((u) => u.name), ["Ash"]);
  assert.deepEqual(problems, [
    "TRACKER_USERS entry 1: the passcode needs at least 10 characters",
    "TRACKER_USERS entry 3: that name is already taken",
    "TRACKER_USERS entry 4: a name is a letter, then letters, digits, - or _",
    "TRACKER_USERS entry 5: the passcode needs at least 10 characters",
  ]);
  for (const problem of problems) assert.doesNotMatch(problem, /nine-char|another|long-enough/);
});

test("without users /me doesn't exist; the rest of the site is never the tracker's business", () => {
  for (const path of ["/me", "/me/log", "/api/me", "/api/me/live"]) {
    assert.equal(trackerGate(path, undefined, [], NOW), "off");
  }
  for (const path of ["/", "/meet", "/api/meta", "/api/live", "/g/1/3929630", "/mex"]) {
    assert.equal(isTrackerPath(path), false, path);
    assert.equal(trackerGate(path, undefined, USERS, NOW), "open");
  }
});

test("with users, pages get the sign-in form and API calls are refused", () => {
  assert.equal(trackerGate("/me", undefined, USERS, NOW), "form");
  assert.equal(trackerGate("/me/review", undefined, USERS, NOW), "form");
  assert.equal(trackerGate("/api/me/live", undefined, USERS, NOW), "locked");
  assert.equal(trackerGate("/api/me", undefined, USERS, NOW), "locked");
  assert.equal(trackerGate("/me/unlock/check", undefined, USERS, NOW), "open");  // the form
  assert.equal(trackerGate("/me/unlock/checkx", undefined, USERS, NOW), "form");
});

test("the cookie names its user, for 30 days, signed with that user's passcode", () => {
  const pass = trackerPass(CHRIS, NOW);
  assert.match(pass, /^Chris\.\d+\.[\w-]+$/);
  assert.equal(trackerUserFrom(pass, USERS, NOW), "Chris");
  assert.equal(trackerGate("/me", pass, USERS, NOW), "open");
  assert.equal(trackerGate("/api/me/live", pass, USERS, NOW + (TRACKER_SECONDS - 1) * 1000),
               "open");
  assert.equal(trackerUserFrom(pass, USERS, NOW + TRACKER_SECONDS * 1000), null);  // expired
  // Chris's passcode changes: Chris is signed out, Ash isn't.
  const changed = parseTrackerUsers("Chris:a-new-passcode-1,Ash:harbor-violet-3307").users;
  assert.equal(trackerUserFrom(pass, changed, NOW), null);
  assert.equal(trackerUserFrom(trackerPass(ASH, NOW), changed, NOW), "Ash");
});

test("a cookie can't be turned into someone else's", () => {
  const same = parseTrackerUsers("Chris:shared-passcode-1,Ash:shared-passcode-1").users;
  const pass = trackerPass(same[0], NOW);
  assert.equal(trackerUserFrom(pass.replace(/^Chris/, "Ash"), same, NOW), null);
  assert.equal(trackerUserFrom(pass.replace(/^Chris/, "chris"), same, NOW), null);
  for (const value of [undefined, "", "Chris", ".1.x", `${pass}x`, `${pass}.extra`, "Pat.1.x"]) {
    assert.equal(trackerUserFrom(value, USERS, NOW), null, String(value));
  }
});

test("signing in needs the user's own passcode; the name's case and spaces don't matter", () => {
  assert.equal(signIn(USERS, "Chris", "lantern-quartz-5512"), CHRIS);
  assert.equal(signIn(USERS, " chris ", " lantern-quartz-5512\n"), CHRIS);
  assert.equal(signIn(USERS, "ASH", "harbor-violet-3307"), ASH);
  for (const [name, code] of [["Chris", "harbor-violet-3307"], ["Ash", "lantern-quartz-5512"],
                              ["Pat", "lantern-quartz-5512"], ["", ""],
                              ["Chris", "LANTERN-QUARTZ-5512"]]) {
    assert.equal(signIn(USERS, name, code), null, `${name}/${code}`);
  }
  assert.equal(signIn([], "Chris", "lantern-quartz-5512"), null);
});

test("after signing in, only a page under /me", () => {
  assert.equal(trackerNext("/me/review?x=1"), "/me/review?x=1");
  assert.equal(trackerNext("/me"), "/me");
  for (const next of [null, undefined, "", "/", "/g/1/2", "https://evil.example",
                      "//evil.example", "/\\evil.example", "/me/unlock/check", "/meet",
                      "/me\nx", "/api/me/live"]) {
    assert.equal(trackerNext(next), "/me", String(next));
  }
});

test("the sign-in form posts the page asked for, escaped, and says when it was wrong", () => {
  const html = trackerUnlockHtml('/me?x="><script>', false);
  assert.match(html, /<form method="post" action="\/me\/unlock\/check">/);
  assert.match(html, /name="name"/);
  assert.match(html, /name="passcode" type="password"/);
  assert.match(html, /value="\/me\?x=&quot;&gt;&lt;script&gt;"/);
  assert.doesNotMatch(html, /<script>/);
  assert.doesNotMatch(html, /don't match/);
  assert.match(trackerUnlockHtml("/me", true), /role="alert">That name and passcode don't match/);
});

test("the public proxy's allowlist never reaches the tracker", () => {
  for (const path of ["me", "me/live", "me/slips", "me/health", "me/games", "me/analytics",
                      "me/settings/tags", "me/slips/1/payout", "me/legs/1"]) {
    assert.equal(isPublicPath("GET", path), false, path);
    assert.equal(isPublicPath("POST", path), false, path);
  }
  assert.equal(isPublicPath("GET", "live"), true);
  assert.equal(isPublicPath("GET", "player/1/2"), true);
  assert.equal(isPublicPath("POST", "lookup"), true);
  assert.equal(isPublicPath("GET", "lookup"), false);
});
