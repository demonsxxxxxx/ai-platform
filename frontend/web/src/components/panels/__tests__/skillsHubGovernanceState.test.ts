import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

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

test("skills hub trusts admin catalog effective permissions over stale auth projection", () => {
  const state = resolveSkillsHubGovernance({
    requestedTab: "skills",
    isAuthenticated: true,
    canReadSkills: false,
    canReadMarketplace: false,
    effectivePermissions: ["skill:admin"],
  });

  assert.equal(state.pageState, "ready");
  assert.equal(state.hasPermission, true);
  assert.equal(state.authProjectionHasPermission, false);
  assert.equal(state.effectiveProjectionHasPermission, true);
  assert.equal(state.effectivePermissionsSource, "catalog");
});

test("ordinary public read projections keep admin skill management fail-closed", () => {
  const common = {
    isAuthenticated: true,
    canReadSkills: false,
    canReadMarketplace: false,
    effectivePermissions: ["skill:read", "marketplace:read"],
    effectivePermissionsKnown: true,
    catalogReadResolved: true,
  };

  const skills = resolveSkillsHubGovernance({
    ...common,
    requestedTab: "skills",
  });
  const marketplace = resolveSkillsHubGovernance({
    ...common,
    requestedTab: "marketplace",
  });

  assert.equal(skills.pageState, "forbidden");
  assert.equal(skills.hasPermission, false);
  assert.equal(skills.requiredPermission, "skill:admin");
  assert.equal(skills.effectivePermissionsSource, "catalog");
  assert.equal(marketplace.pageState, "forbidden");
  assert.equal(marketplace.hasPermission, false);
  assert.equal(marketplace.requiredPermission, "marketplace:admin");
  assert.equal(marketplace.effectivePermissionsSource, "catalog");
});

test("skills hub treats permission-denied catalog probes as fail-closed", () => {
  const state = resolveSkillsHubGovernance({
    requestedTab: "marketplace",
    isAuthenticated: true,
    canReadSkills: true,
    canReadMarketplace: true,
    effectivePermissions: ["marketplace:admin"],
    catalogPermissionDenied: true,
  });

  assert.equal(state.pageState, "forbidden");
  assert.equal(state.hasPermission, false);
  assert.equal(state.governedUnavailable, true);
  assert.equal(state.requiredPermission, "marketplace:admin");
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

test("marketplace panel derives shared-management affordances from ai-admin gating instead of owner fallback", () => {
  const source = readFileSync(
    join(import.meta.dirname, "..", "MarketplacePanel.tsx"),
    "utf8",
  );

  assert.doesNotMatch(source, /userEffectivePermissions/);
  assert.doesNotMatch(source, /\(skill\.is_owner \|\| canAdmin\)/);
  assert.match(source, /canManageSharedMarketplace\(\{/);
  assert.match(source, /isAiAdminUser\(user\)/);
});
