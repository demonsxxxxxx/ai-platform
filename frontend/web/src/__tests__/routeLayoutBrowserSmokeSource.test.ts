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
    "/agent-market/agt_support",
    "/agent-market/agt_support/chat",
    "/agent-builder",
    "/plugins",
    "/runs",
    "/ai-apps/sop-assistant",
    "/ai-apps/word-review",
  ]) {
    assert.match(source, new RegExp(path.replaceAll("/", "\\/")));
  }
  assert.match(source, /bodyScrollWidth > layout\.viewportWidth/);
  assert.match(source, /target\.scrollTop = target\.scrollHeight/);
  assert.match(source, /overlay_clipped/);
  assert.match(source, /role=listbox/);
  assert.match(source, /drawer-contained/);
  assert.match(source, /model_group_drawer_width_failed/);
  assert.match(source, /geometry\.leftGap - geometry\.rightGap/);
  assert.match(source, /geometry\.scrollWidth > geometry\.clientWidth/);
  assert.match(source, /models:group-focus-restored/);
  assert.ok(source.includes('group header meets left card edge'));
  assert.ok(source.includes('group header meets right card edge'));
  assert.ok(source.includes('new group shares the search row'));
  assert.ok(source.includes('groupListGeometry.actionsReachable && groupListGeometry.lastRowReachable'));
  assert.ok(source.includes('models:group-empty-search'));
  assert.ok(source.includes('models:group-single-search'));
  assert.ok(source.includes('group create form stays reachable'));
  assert.match(source, /skill-distribution-receipt/);
  assert.match(source, /if \(failures\.length\) process\.exitCode = 1/);
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
  assert.match(source, /responsive_layout_failed/);
  assert.match(source, /detailContained/);
  assert.match(source, /tableReachable/);
  assert.match(source, /uploadBottom > viewport.height/);
  assert.match(source, /plugin_layout_failed/);
  assert.match(source, /plugins:auth-tab-wrap/);
  assert.match(source, /plugins:auth-focus-restored/);
  assert.match(source, /plugins:collapsed-icon-not-centered/);
  assert.match(source, /data-agent-market-start-chat/);
  assert.match(source, /data-agent-builder-save-reason/);
  assert.match(source, /parent_observation_id/);
  assert.match(source, /runs:observation-identities-not-collapsed/);
  assert.match(source, /runs:receipt-parent-missing/);
  assert.match(source, /runs:focus-restored/);
  assert.ok(source.includes("const providerAModels = Array.from({ length: 29 }"));
  assert.ok(source.includes("const providerBModels = [0, 1].map"));
  assert.ok(source.includes("provider: 'compatible', provider_id: 'supplier-b'"));
  assert.ok(source.includes("url.pathname === '/api/ai/admin/model-providers/legacy'"));
  assert.ok(source.includes("url.pathname === '/api/ai/admin/models'"));
  assert.ok(source.includes("models: [...providerAModels, ...providerBModels]"));
  assert.ok(source.includes("models:provider-entry-global-catalog"));
  assert.ok(source.includes("assignedIsNormal: Boolean(assigned && !assigned.querySelector('[data-group-model-missing]'))"));
  assert.ok(source.includes('[data-model-group-catalog-model="mdl-b1"]'));
  assert.ok(source.includes("request.method === 'PUT').at(-1)?.body"));
  assert.ok(source.includes("body?.default_model_id === null"));
  assert.ok(source.includes("nullPayload.models.every((model) => model.enabled === false)"));
  assert.ok(source.includes("models:reenabled-needs-default"));
  assert.ok(source.includes('const modelRowSwitch = (modelId) =>'));
});
