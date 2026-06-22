import test from "node:test";
import assert from "node:assert/strict";

import { resolveSkillsHubTab } from "../state.ts";
import { resolveFrontendGovernanceState } from "../../../governance/frontendGovernanceState.ts";

test("keeps the requested tab when both permissions are available", () => {
  assert.equal(resolveSkillsHubTab(undefined, true, true), "skills");
  assert.equal(resolveSkillsHubTab("skills", true, true), "skills");
  assert.equal(resolveSkillsHubTab("marketplace", true, true), "marketplace");
});

test("resolves to local skills when only local skills are available", () => {
  assert.equal(resolveSkillsHubTab(undefined, true, false), "skills");
  assert.equal(resolveSkillsHubTab("skills", true, false), "skills");
});

test("resolves to marketplace when only marketplace is available", () => {
  assert.equal(resolveSkillsHubTab(undefined, false, true), "marketplace");
  assert.equal(resolveSkillsHubTab("marketplace", false, true), "marketplace");
});

test("keeps explicitly requested discovery tabs for fail-closed page bodies", () => {
  assert.equal(resolveSkillsHubTab("marketplace", true, false), "marketplace");
  assert.equal(resolveSkillsHubTab("skills", false, true), "skills");
});

test("defaults to marketplace when no discovery permissions are available", () => {
  assert.equal(resolveSkillsHubTab(undefined, false, false), "marketplace");
  assert.equal(resolveSkillsHubTab("skills", false, false), "skills");
  assert.equal(resolveSkillsHubTab("marketplace", false, false), "marketplace");
});

test("keeps backend public read permission authoritative when settings are degraded", () => {
  assert.equal(
    resolveFrontendGovernanceState({
      isAuthenticated: true,
      hasPermission: true,
      projectionError: "settings projection unavailable",
    }),
    "degraded",
  );
});
