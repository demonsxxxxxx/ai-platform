import test from "node:test";
import assert from "node:assert/strict";

import {
  resolveSkillsHubGovernance,
  resolveSkillsHubTab,
} from "../state.ts";
import { resolveFrontendGovernanceState } from "../../../governance/frontendGovernanceState.ts";

test("keeps explicit skills hub tab requests stable", () => {
  assert.equal(resolveSkillsHubTab(undefined, true, true), "skills");
  assert.equal(resolveSkillsHubTab("skills", true, true), "skills");
  assert.equal(resolveSkillsHubTab("marketplace", true, true), "marketplace");
  assert.equal(resolveSkillsHubTab(undefined, true, false), "skills");
  assert.equal(resolveSkillsHubTab(undefined, false, true), "marketplace");
  assert.equal(resolveSkillsHubTab(undefined, false, false), "marketplace");
});

test("keeps backend permission truth authoritative when unrelated settings degrade", () => {
  assert.equal(
    resolveFrontendGovernanceState({
      isAuthenticated: true,
      hasPermission: true,
      projectionError: "settings projection unavailable",
    }),
    "degraded",
  );
});

test("maps the skills route to the admin skill management contract", () => {
  const state = resolveSkillsHubGovernance({
    requestedTab: "skills",
    isAuthenticated: true,
    canReadSkills: true,
    canReadMarketplace: false,
  });

  assert.equal(state.hasPermission, true);
  assert.equal(state.authProjectionHasPermission, true);
  assert.equal(state.effectiveProjectionHasPermission, false);
  assert.equal(state.governedUnavailable, false);
  assert.equal(state.requiredPermission, "skill:admin");
  assert.equal(state.degraded, false);
  assert.equal(state.pageState, "ready");
  assert.equal(state.effectivePermissionsSource, "auth");
});

test("maps the marketplace compatibility tab to the admin marketplace contract", () => {
  const state = resolveSkillsHubGovernance({
    requestedTab: "marketplace",
    isAuthenticated: true,
    canReadSkills: false,
    canReadMarketplace: true,
    projectionError: "marketplace projection unavailable",
  });

  assert.equal(state.requiredPermission, "marketplace:admin");
  assert.equal(state.hasPermission, true);
  assert.equal(state.degraded, true);
  assert.equal(state.pageState, "degraded");
});

test("keeps missing admin grants degraded while the catalog probe is unresolved", () => {
  const state = resolveSkillsHubGovernance({
    requestedTab: "skills",
    isAuthenticated: true,
    canReadSkills: false,
    canReadMarketplace: false,
  });

  assert.equal(state.hasPermission, false);
  assert.equal(state.authProjectionHasPermission, false);
  assert.equal(state.effectiveProjectionHasPermission, false);
  assert.equal(state.governedUnavailable, false);
  assert.equal(state.pageState, "degraded");
  assert.equal(state.effectivePermissionsSource, "probe");
});

test("keeps admin catalog probes loading without declaring permission", () => {
  const skills = resolveSkillsHubGovernance({
    requestedTab: "skills",
    isAuthenticated: true,
    canReadSkills: false,
    canReadMarketplace: false,
    catalogReadPending: true,
  });
  const marketplace = resolveSkillsHubGovernance({
    requestedTab: "marketplace",
    isAuthenticated: true,
    canReadSkills: false,
    canReadMarketplace: false,
    catalogReadPending: true,
  });

  assert.equal(skills.pageState, "loading");
  assert.equal(skills.hasPermission, false);
  assert.equal(skills.governedUnavailable, false);
  assert.equal(skills.effectivePermissionsSource, "probe");
  assert.equal(marketplace.pageState, "loading");
  assert.equal(marketplace.hasPermission, false);
  assert.equal(marketplace.governedUnavailable, false);
  assert.equal(marketplace.effectivePermissionsSource, "probe");
});

test("keeps auth-granted admin catalog routes ready while effective permissions are not projected", () => {
  const skills = resolveSkillsHubGovernance({
    requestedTab: "skills",
    isAuthenticated: true,
    canReadSkills: true,
    canReadMarketplace: false,
    effectivePermissions: [],
    effectivePermissionsKnown: false,
    catalogReadResolved: true,
  });
  const marketplace = resolveSkillsHubGovernance({
    requestedTab: "marketplace",
    isAuthenticated: true,
    canReadSkills: false,
    canReadMarketplace: true,
    effectivePermissions: [],
    effectivePermissionsKnown: false,
    catalogReadResolved: true,
  });

  assert.equal(skills.pageState, "ready");
  assert.equal(skills.degraded, false);
  assert.equal(skills.effectivePermissionsSource, "auth");
  assert.equal(marketplace.pageState, "ready");
  assert.equal(marketplace.degraded, false);
  assert.equal(marketplace.effectivePermissionsSource, "auth");
});

test("uses backend admin effective permissions to mark skills ready after catalog load", () => {
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
  assert.equal(state.requiredPermission, "skill:admin");
});

test("uses backend marketplace admin effective permissions to mark marketplace ready after catalog load", () => {
  const state = resolveSkillsHubGovernance({
    requestedTab: "marketplace",
    isAuthenticated: true,
    canReadSkills: false,
    canReadMarketplace: false,
    effectivePermissions: ["marketplace:admin"],
  });

  assert.equal(state.pageState, "ready");
  assert.equal(state.hasPermission, true);
  assert.equal(state.authProjectionHasPermission, false);
  assert.equal(state.effectiveProjectionHasPermission, true);
  assert.equal(state.requiredPermission, "marketplace:admin");
});

test("keeps public read catalog projections fail-closed for admin skill management", () => {
  const state = resolveSkillsHubGovernance({
    requestedTab: "marketplace",
    isAuthenticated: true,
    canReadSkills: false,
    canReadMarketplace: false,
    catalogReadResolved: true,
    effectivePermissions: ["skill:read", "marketplace:read"],
    effectivePermissionsKnown: true,
  });

  assert.equal(state.pageState, "forbidden");
  assert.equal(state.hasPermission, false);
  assert.equal(state.governedUnavailable, true);
  assert.equal(state.effectivePermissionsSource, "catalog");
});

test("keeps legacy marketplace array responses forbidden without an admin grant", () => {
  const state = resolveSkillsHubGovernance({
    requestedTab: "marketplace",
    isAuthenticated: true,
    canReadSkills: false,
    canReadMarketplace: false,
    catalogReadResolved: true,
    effectivePermissions: [],
    effectivePermissionsKnown: false,
  });

  assert.equal(state.pageState, "forbidden");
  assert.equal(state.hasPermission, false);
  assert.equal(state.catalogReadResolved, true);
  assert.equal(state.effectivePermissionsSource, "catalog");
  assert.equal(state.degraded, false);
});

test("keeps catalog permission denial authoritative over projection degradation", () => {
  const state = resolveSkillsHubGovernance({
    requestedTab: "marketplace",
    isAuthenticated: true,
    canReadSkills: false,
    canReadMarketplace: false,
    catalogPermissionDenied: true,
    projectionError: "settings projection unavailable",
  });

  assert.equal(state.hasPermission, false);
  assert.equal(state.governedUnavailable, true);
  assert.equal(state.pageState, "forbidden");
});

test("maps authenticated accounts without a workspace before permission denial", () => {
  const state = resolveSkillsHubGovernance({
    requestedTab: "marketplace",
    isAuthenticated: true,
    hasWorkspace: false,
    canReadSkills: true,
    canReadMarketplace: true,
  });

  assert.equal(state.pageState, "no-workspace");
  assert.equal(state.hasPermission, true);
  assert.equal(state.governedUnavailable, false);
});
