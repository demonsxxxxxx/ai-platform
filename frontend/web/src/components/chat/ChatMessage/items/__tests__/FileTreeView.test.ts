import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("binary file downloads use the unified preview download helper", () => {
  const source = readFileSync(
    new URL("../FileTreeView.tsx", import.meta.url),
    "utf8",
  );

  assert.match(source, /downloadPreviewUrl/);
  assert.doesNotMatch(
    source,
    /if\s*\(\s*isBinary\s*&&\s*url\s*\)[\s\S]*?a\.href\s*=\s*url[\s\S]*?a\.click\(\)/,
    "binary files must not click a raw URL anchor directly",
  );
});
