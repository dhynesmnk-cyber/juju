// `npm test`: Node's own test runner, no network.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  TRACKER_SECONDS, matchOwner, ownerOf, parseOwners, safeMeNext, signOwner, trackerGate,
} from "./trackerPass.ts";
import { trackerUnlockHtml } from "./unlockPage.ts";

const OWNERS = parseOwners("chris:kite-ember-9417, ash:lark-cinder-2286")!;
const NOW = 1_790_000_000_000;

test("owners are name:passcode pairs; anything else turns the section off", () => {
  assert.deepEqual([...OWNERS], [["chris", "kite-ember-9417"], ["ash", "lark-cinder-2286"]]);
  for (const text of [undefined, "", "chris", "chris:short", "Chris:kite-ember-9417",
                      "chris:kite-ember-9417,chris:lark-cinder-2286", "chris:kite:ember-9417",
                      "chris:kite-ember-9417,", "chris:kite ember 9417"]) {
    assert.equal(parseOwners(text), null, String(text));
  }
});

test("a cookie says who signed in, for 30 days", () => {
  const cookie = signOwner("chris", "kite-ember-9417", NOW);
  assert.match(cookie, /^chris\.\d+\.[\w-]+$/);
  assert.equal(ownerOf(cookie, OWNERS, NOW), "chris");
  assert.equal(ownerOf(cookie, OWNERS, NOW + (TRACKER_SECONDS - 1) * 1000), "chris");
  assert.equal(ownerOf(cookie, OWNERS, NOW + TRACKER_SECONDS * 1000), null);
  assert.equal(ownerOf(cookie, null, NOW), null);
  for (const bad of [undefined, "", "chris", ".1.x", `${cookie}x`, `bob${cookie.slice(5)}`]) {
    assert.equal(ownerOf(bad, OWNERS, NOW), null, String(bad));
  }
});

test("a cookie can't be renamed to the other owner, even with the same passcode", () => {
  const same = parseOwners("chris:kite-ember-9417,ash:kite-ember-9417")!;
  const cookie = signOwner("chris", "kite-ember-9417", NOW);
  assert.equal(ownerOf(`ash${cookie.slice("chris".length)}`, same, NOW), null);
});

test("a new passcode signs out only its owner", () => {
  const chris = signOwner("chris", "kite-ember-9417", NOW);
  const ash = signOwner("ash", "lark-cinder-2286", NOW);
  const changed = parseOwners("chris:a-new-passcode-1,ash:lark-cinder-2286");
  assert.equal(ownerOf(chris, changed, NOW), null);
  assert.equal(ownerOf(ash, changed, NOW), "ash");
});

test("signing in takes the name in any case and that owner's own passcode", () => {
  assert.equal(matchOwner(OWNERS, "chris", "kite-ember-9417"), "chris");
  assert.equal(matchOwner(OWNERS, " Chris ", " kite-ember-9417\n"), "chris");
  assert.equal(matchOwner(OWNERS, "ash", "kite-ember-9417"), null);  // the other's passcode
  assert.equal(matchOwner(OWNERS, "bob", "kite-ember-9417"), null);
  assert.equal(matchOwner(OWNERS, "chris", ""), null);
});

test("only /me and /api/me are the owners'; off without owners, then signed in or not", () => {
  const cookie = signOwner("ash", "lark-cinder-2286", NOW);
  for (const path of ["/", "/g/1/3929630", "/api/status", "/meet", "/api/meetings"]) {
    assert.equal(trackerGate(path, undefined, OWNERS, NOW), "pass", path);
  }
  assert.equal(trackerGate("/me", undefined, null, NOW), "off");
  assert.equal(trackerGate("/api/me/recaps", cookie, null, NOW), "off");
  assert.equal(trackerGate("/me", undefined, OWNERS, NOW), "form");
  assert.equal(trackerGate("/me/recaps/1", undefined, OWNERS, NOW), "form");
  assert.equal(trackerGate("/api/me/recaps", undefined, OWNERS, NOW), "locked");
  assert.equal(trackerGate("/me/unlock/check", undefined, OWNERS, NOW), "open");
  assert.equal(trackerGate("/me/signout", undefined, OWNERS, NOW), "open");
  assert.equal(trackerGate("/me", cookie, OWNERS, NOW), "open");
  assert.equal(trackerGate("/api/me/lookups", cookie, OWNERS, NOW), "open");
});

test("after signing in, a path on this site and never the sign-in itself", () => {
  assert.equal(safeMeNext("/me/recaps/4"), "/me/recaps/4");
  assert.equal(safeMeNext("/g/1/3929630?play=run"), "/g/1/3929630?play=run");
  for (const next of ["/me/unlock/check", "/me/signout", "/me/unlockx"]) {
    assert.equal(safeMeNext(next), "/me", next);
  }
  for (const next of ["", "https://evil.example", "//evil.example", "/unlock/check"]) {
    assert.equal(safeMeNext(next), "/", next);
  }
});

test("the sign-in form posts the page asked for, escaped, and says when it was wrong", () => {
  const html = trackerUnlockHtml('/me/recaps/1?x="><script>', false);
  assert.match(html, /<form method="post" action="\/me\/unlock\/check">/);
  assert.match(html, /value="\/me\/recaps\/1\?x=&quot;&gt;&lt;script&gt;"/);
  assert.match(html, /name="name"/);
  assert.doesNotMatch(html, /<script>/);
  assert.doesNotMatch(html, /didn't match/);
  assert.match(trackerUnlockHtml("/me", true), /role="alert">That name and passcode didn't match/);
});
