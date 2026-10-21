// `npm test`: Node's own test runner, no network.
import assert from "node:assert/strict";
import { test } from "node:test";
import { isPublicApi } from "./publicApi.ts";

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
