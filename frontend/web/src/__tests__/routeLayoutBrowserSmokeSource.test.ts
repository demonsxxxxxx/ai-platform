import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

test("offline route layout smoke covers required routes, viewports, reachability, and overlays", () => {
  const source = readFileSync(
    join(process.cwd(), "scripts/route-layout-browser-smoke.mjs"),
    "utf8",
  );

  assert.match(source, /mock_backed: true/);
  assert.match(source, /width: 1440, height: 900/);
  assert.match(source, /width: 768, height: 900/);
  assert.match(source, /width: 390, height: 844/);
  for (const path of [
    "/skills",
    "/agent-market",
    "/agent-market/agt_support/1",
    "/agent-market/agt_support/1/chat",
    "/agent-builder",
  ]) {
    assert.match(source, new RegExp(path.replaceAll("/", "\\/")));
  }
  assert.match(source, /bodyScrollWidth > layout\.viewportWidth/);
  assert.match(source, /target\.scrollTop = target\.scrollHeight/);
  assert.match(source, /overlay_clipped/);
  assert.match(source, /department-selector__menu/);
});
