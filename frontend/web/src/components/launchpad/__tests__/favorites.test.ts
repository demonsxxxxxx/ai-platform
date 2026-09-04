import assert from "node:assert/strict";
import test from "node:test";

import { parseLaunchpadFavoriteIds } from "../favorites.ts";

const allowedIds = new Set(["内网登录:OA", "AI:Gemini"]);

test("favorites keep unique known server metadata ids in user order", () => {
  assert.deepEqual(
    parseLaunchpadFavoriteIds(
      ["AI:Gemini", "missing", "AI:Gemini", "内网登录:OA"],
      allowedIds,
    ),
    ["AI:Gemini", "内网登录:OA"],
  );
});

test("favorites recover safely from malformed server metadata", () => {
  assert.deepEqual(parseLaunchpadFavoriteIds("AI:Gemini", allowedIds), []);
  assert.deepEqual(parseLaunchpadFavoriteIds({ id: "AI:Gemini" }, allowedIds), []);
  assert.deepEqual(parseLaunchpadFavoriteIds(null, allowedIds), []);
});
