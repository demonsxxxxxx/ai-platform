import test from "node:test";
import assert from "node:assert/strict";

import {
  buildPersonaPresetListUrl,
  buildPersonaPresetPreferenceUrl,
} from "../personaPreset.ts";

test("buildPersonaPresetPreferenceUrl encodes preset ids", () => {
  assert.equal(
    buildPersonaPresetPreferenceUrl("preset/1"),
    "/api/persona-presets/preset%2F1/preference",
  );
});

test("buildPersonaPresetListUrl keeps page-sized pagination params", () => {
  assert.equal(
    buildPersonaPresetListUrl({ skip: 12, limit: 12, q: "planner" }),
    "/api/persona-presets/?q=planner&skip=12&limit=12",
  );
});
