import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const currentDir = dirname(fileURLToPath(import.meta.url));
const selectorSource = readFileSync(
  resolve(currentDir, "../PersonaPresetSelector.tsx"),
  "utf8",
);
const previewSource = readFileSync(
  resolve(currentDir, "../PersonaPreviewSidebar.tsx"),
  "utf8",
);
const personaCss = readFileSync(
  resolve(currentDir, "../../../styles/persona.css"),
  "utf8",
);

test("persona preset use buttons expose per-preset loading feedback", () => {
  assert.match(selectorSource, /pendingUsePresetId/);
  assert.match(selectorSource, /setPendingUsePresetId\(preset\.id\)/);
  assert.match(selectorSource, /finally\s*\{\s*setPendingUsePresetId\(null\);/);
  assert.match(selectorSource, /aria-busy=\{isUsingPreset\}/);
  assert.match(selectorSource, /<Loader2 size=\{13\} className="animate-spin"/);
  assert.match(selectorSource, /personaPresets\.applying/);
});

test("persona preview use button can mirror selector loading state", () => {
  assert.match(previewSource, /isUsingPreset/);
  assert.match(previewSource, /aria-busy=\{isUsingPreset\}/);
  assert.match(previewSource, /<Loader2 size=\{14\} className="animate-spin"/);
  assert.match(previewSource, /personaPresets\.applying/);
});

test("persona preset loading buttons keep disabled cursor distinct", () => {
  assert.match(personaCss, /\.pps-card__action--loading/);
  assert.match(personaCss, /cursor: wait;/);
});
