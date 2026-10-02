import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();

function read(path: string): string {
  return readFileSync(join(root, path), "utf8");
}

test("librechat shell geometry keeps the approved rail and panel widths", () => {
  const source = read("src/librechat-ui/surface.ts");

  assert.match(source, /railWidthPx:\s*52/);
  assert.match(source, /expandedMinWidthPx:\s*288/);
  assert.match(source, /mobileMaxWidth:\s*"min\(85vw, 380px\)"/);
});
