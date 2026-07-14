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
  assert.equal(getWorkbenchNavItemFromPathname("/files"), "files");
  assert.equal(getWorkbenchNavItemFromPathname("/mcp"), "mcp");
  assert.equal(getWorkbenchNavItemFromPathname("/models"), "models");
  assert.equal(getWorkbenchNavItemFromPathname("/roles"), null);
  assert.equal(getWorkbenchNavItemFromPathname("/chat"), null);
});

test("safe navigation redirects unauthorized management destinations before routing", () => {
  assert.equal(getSafeWorkbenchNavPath("models", null), "/chat");
  assert.equal(getSafeWorkbenchNavPath("mcp", { is_admin: false }), "/mcp");
});
