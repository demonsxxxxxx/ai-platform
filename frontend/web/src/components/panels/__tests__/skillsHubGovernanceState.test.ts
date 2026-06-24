import test from "node:test";
import assert from "node:assert/strict";

import { resolveSkillsHubGovernance } from "../SkillsHubPanel/state.ts";

test("skills hub exposes all frontend governance states explicitly", () => {
  const base = {
    requestedTab: "skills" as const,
    isAuthenticated: true,
    canReadSkills: true,
    canReadMarketplace: true,
  };

  assert.equal(
    resolveSkillsHubGovernance({ ...base, isLoading: true }).pageState,
    "loading",
  );
  assert.equal(
    resolveSkillsHubGovernance({
      ...base,
      isAuthenticated: false,
    }).pageState,
    "logged-out",
  );
  assert.equal(
    resolveSkillsHubGovernance({
      ...base,
      hasWorkspace: false,
    }).pageState,
    "no-workspace",
  );
  assert.equal(
    resolveSkillsHubGovernance({
      ...base,
      catalogPermissionDenied: true,
    }).pageState,
    "forbidden",
  );
  assert.equal(
    resolveSkillsHubGovernance({
      ...base,
      projectionError: "catalog temporarily unavailable",
    }).pageState,
    "degraded",
  );
  assert.equal(resolveSkillsHubGovernance(base).pageState, "ready");
});

test("skills hub trusts public catalog effective permissions over stale auth projection", () => {
  const state = resolveSkillsHubGovernance({
    requestedTab: "skills",
    isAuthenticated: true,
    canReadSkills: false,
    canReadMarketplace: false,
    effectivePermissions: ["skill:read", "marketplace:read"],
  });

  assert.equal(state.pageState, "ready");
  assert.equal(state.hasPermission, true);
  assert.equal(state.authProjectionHasPermission, false);
  assert.equal(state.effectiveProjectionHasPermission, true);
  assert.equal(state.effectivePermissionsSource, "catalog");
});

test("skills hub treats permission-denied catalog probes as fail-closed", () => {
  const state = resolveSkillsHubGovernance({
    requestedTab: "marketplace",
    isAuthenticated: true,
    canReadSkills: true,
    canReadMarketplace: true,
    effectivePermissions: ["skill:read", "marketplace:read"],
    catalogPermissionDenied: true,
  });

  assert.equal(state.pageState, "forbidden");
  assert.equal(state.hasPermission, false);
  assert.equal(state.governedUnavailable, true);
  assert.equal(state.requiredPermission, "marketplace:read");
});

test("skills hub recognizes admin permissions as read permission", () => {
  const skills = resolveSkillsHubGovernance({
    requestedTab: "skills",
    isAuthenticated: true,
    canReadSkills: false,
    canReadMarketplace: false,
    effectivePermissions: ["skill:admin"],
  });
  const marketplace = resolveSkillsHubGovernance({
    requestedTab: "marketplace",
    isAuthenticated: true,
    canReadSkills: false,
    canReadMarketplace: false,
    effectivePermissions: ["marketplace:admin"],
  });

  assert.equal(skills.pageState, "ready");
  assert.equal(marketplace.pageState, "ready");
});
