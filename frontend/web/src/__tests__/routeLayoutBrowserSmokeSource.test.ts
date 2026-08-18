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
  assert.match(source, /route_layout_smoke_unstubbed_api/);
  assert.match(source, /requiredSelectors/);
  assert.match(source, /element\.complete/);
  assert.match(source, /element\.naturalWidth > 0/);
  assert.match(source, /element\.naturalHeight > 0/);
  assert.match(source, /rect\.width > 0/);
  assert.match(source, /rect\.height > 0/);
  assert.match(source, /requiredRequests/);
  assert.match(source, /missingRequests/);
  assert.match(source, /data-selected-skill-detail-shell/);
  assert.match(source, /skill-opaque-42/);
  assert.match(source, /data-skill-distribution-save/);
  assert.match(source, /skill-distribution-save/);
  assert.match(source, /data-agent-market-start-chat/);
  assert.match(source, /data-agent-builder-save-reason/);
});
