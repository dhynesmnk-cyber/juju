// `npm test`: Node's own test runner, no network.
import assert from "node:assert/strict";
import { test } from "node:test";
import { isLiveChat, isPublicApi, liveChatOpen } from "./publicApi.ts";

test("the public door forwards the UI endpoints", () => {
  for (const path of ["live", "status", "suggest", "game/1", "player/1/3929630", "team/1/3",
                      "parlay/1"]) {
    assert.equal(isPublicApi("GET", path), true, path);
  }
  assert.equal(isPublicApi("POST", "lookup"), true);
  assert.equal(isPublicApi("GET", "lookup"), false);
});

test("the owners' API is never on it (docs/licensing.md)", () => {
  for (const path of ["me", "me/recaps", "me/recaps/1", "me/lookups", "me/lookups/1",
                      "me/history/player/3929630"]) {
    assert.equal(isPublicApi("GET", path), false, path);
    assert.equal(isPublicApi("POST", path), false, path);
  }
});

test("the live chat's endpoints are on the public door, and nothing near them", () => {
  for (const path of ["live/games", "live/markets", "live/plan", "live-markets"]) {
    assert.equal(isPublicApi("GET", path), true, path);
    assert.equal(isPublicApi("POST", path), false, path);
    assert.equal(isLiveChat(path), true, path);
  }
  assert.equal(isPublicApi("POST", "live/ask"), true);
  assert.equal(isPublicApi("GET", "live/ask"), false);
  for (const path of ["live/", "live/other", "live/games/1", "live-markets/x", "live/ask/x",
                      "live-markets-all"]) {
    assert.equal(isPublicApi("GET", path), false, path);
    assert.equal(isPublicApi("POST", path), false, path);
  }
  // the archive's own `live` list is not the live chat
  assert.equal(isLiveChat("live"), false);
  assert.equal(isPublicApi("GET", "live"), true);
});

test("the live chat is off unless the site has a passcode", () => {
  assert.equal(liveChatOpen(undefined), false);
  assert.equal(liveChatOpen(""), false);
  assert.equal(liveChatOpen("a-real-passcode"), true);
});
