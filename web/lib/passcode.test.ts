// `npm test`: Node's own test runner, no network.
import assert from "node:assert/strict";
import { test } from "node:test";
import { PASS_SECONDS, gate, passcodeMatches, safeNext, unlockPass } from "./passcode.ts";
import { unlockHtml } from "./unlockPage.ts";

const CODE = "kite-ember-9417";
const NOW = 1_790_000_000_000;

test("without a passcode the gate is open: development, CI and Fly as they are", () => {
  for (const path of ["/", "/g/1/3929630", "/api/status", "/unlock/check"]) {
    assert.equal(gate(path, undefined, undefined, NOW), "open");
    assert.equal(gate(path, undefined, "", NOW), "open");
  }
});

test("with one, pages get the passcode form and API calls are refused", () => {
  assert.equal(gate("/", undefined, CODE, NOW), "form");
  assert.equal(gate("/g/1/3929630/image", undefined, CODE, NOW), "form");
  assert.equal(gate("/about", undefined, CODE, NOW), "form");
  assert.equal(gate("/api/status", undefined, CODE, NOW), "locked");
  assert.equal(gate("/api", undefined, CODE, NOW), "locked");
  assert.equal(gate("/apixyz", undefined, CODE, NOW), "form");
  assert.equal(gate("/unlock/check", undefined, CODE, NOW), "open");  // the form posts here
  assert.equal(gate("/unlock/checkx", undefined, CODE, NOW), "form");
});

test("the cookie opens it for 30 days, and only for the passcode that signed it", () => {
  const pass = unlockPass(CODE, NOW);
  assert.equal(gate("/", pass, CODE, NOW), "open");
  assert.equal(gate("/api/status", pass, CODE, NOW + (PASS_SECONDS - 1) * 1000), "open");
  assert.equal(gate("/", pass, CODE, NOW + PASS_SECONDS * 1000), "form");
  assert.equal(gate("/", pass, "a-new-passcode", NOW), "form");  // changed: everyone out
  assert.equal(gate("/", `${pass}x`, CODE, NOW), "form");
});

test("the passcode must match exactly, apart from spaces around what was typed", () => {
  assert.equal(passcodeMatches(CODE, CODE), true);
  assert.equal(passcodeMatches(CODE, ` ${CODE}\n`), true);
  for (const attempt of ["", "kite-ember-941", `${CODE}7`, "KITE-EMBER-9417"]) {
    assert.equal(passcodeMatches(CODE, attempt), false, attempt);
  }
});

test("after unlocking, only a path on this site", () => {
  assert.equal(safeNext("/g/1/3929630?play=run&yards=17"), "/g/1/3929630?play=run&yards=17");
  assert.equal(safeNext("/"), "/");
  for (const next of [null, undefined, "", "https://evil.example", "//evil.example",
                      "/\\evil.example", "evil", "/unlock", "/unlock/check", "/a\nb"]) {
    assert.equal(safeNext(next), "/", String(next));
  }
});

test("the passcode form posts the page asked for, escaped, and says when it was wrong", () => {
  const html = unlockHtml('/g/1/3929630?play=run&x="><script>', false);
  assert.match(html, /<form method="post" action="\/unlock\/check">/);
  assert.match(html, /value="\/g\/1\/3929630\?play=run&amp;x=&quot;&gt;&lt;script&gt;"/);
  assert.doesNotMatch(html, /<script>/);
  assert.doesNotMatch(html, /didn't work/);
  assert.match(unlockHtml("/", true), /role="alert">That passcode didn't work/);
  assert.match(html, /1-800-GAMBLER/);
});
