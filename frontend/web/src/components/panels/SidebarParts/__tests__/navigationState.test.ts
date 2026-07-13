import test from "node:test";
import assert from "node:assert/strict";
import {
  getSafeWorkbenchNavPath,
  getWorkbenchNavItemFromPathname,
} from "../navigationState.ts";

test("maps authenticated workbench routes to sidebar navigation items", () => {
  assert.equal(getWorkbenchNavItemFromPathname("/apps"), "apps");
  assert.equal(getWorkbenchNavItemFromPathname("/skills"), "skills");
  assert.equal(getWorkbenchNavItemFromPathname("/marketplace"), null);
  assert.equal(getWorkbenchNavItemFromPathname("/persona"), "persona");
  assert.equal(getWorkbenchNavItemFromPathname("/files"), "files");
  assert.equal(
    getWorkbenchNavItemFromPathname("/agent-workspace"),
    "agent-workspace",
  );
  assert.equal(getWorkbenchNavItemFromPathname("/mcp"), "mcp");
  assert.equal(
    getWorkbenchNavItemFromPathname("/channels/slack/demo"),
    "channels",
  );
  assert.equal(getWorkbenchNavItemFromPathname("/agents"), "agents");
  assert.equal(getWorkbenchNavItemFromPathname("/models"), "models");
  assert.equal(getWorkbenchNavItemFromPathname("/roles"), null);
  assert.equal(getWorkbenchNavItemFromPathname("/chat"), null);
});

test("safe navigation redirects unauthorized management destinations before routing", () => {
  assert.equal(getSafeWorkbenchNavPath("channels", { is_admin: false }), "/chat");
  assert.equal(getSafeWorkbenchNavPath("models", null), "/chat");
  assert.equal(getSafeWorkbenchNavPath("mcp", { is_admin: false }), "/mcp");
  assert.equal(getSafeWorkbenchNavPath("channels", { is_admin: true }), "/channels");
});
