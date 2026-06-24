import test from "node:test";
import assert from "node:assert/strict";

import {
  resolveSkillsHubGovernance,
  resolveSkillsHubTab,
} from "../state.ts";
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

test("maps the skills route to the public skill read contract", () => {
  const state = resolveSkillsHubGovernance({
    requestedTab: "skills",
    isAuthenticated: true,
    canReadSkills: true,
    canReadMarketplace: false,
  });

  assert.equal(state.hasPermission, true);
  assert.equal(state.authProjectionHasPermission, true);
  assert.equal(state.governedUnavailable, false);
  assert.equal(state.requiredPermission, "skill:read");
  assert.equal(state.degraded, false);
});

test("maps the marketplace route to the public marketplace read contract", () => {
  const state = resolveSkillsHubGovernance({
    requestedTab: "marketplace",
    isAuthenticated: true,
    canReadSkills: true,
    canReadMarketplace: false,
    projectionError: "marketplace projection unavailable",
  });

  assert.equal(state.requiredPermission, "marketplace:read");
  assert.equal(state.degraded, true);
});

test("does not block the catalog probe when auth permissions omit the public read grant", () => {
  const state = resolveSkillsHubGovernance({
    requestedTab: "skills",
    isAuthenticated: true,
    canReadSkills: false,
    canReadMarketplace: false,
  });

  assert.equal(state.hasPermission, true);
  assert.equal(state.authProjectionHasPermission, false);
  assert.equal(state.governedUnavailable, false);
});

test("marks the hub forbidden only after the catalog API proves permission denial", () => {
  const state = resolveSkillsHubGovernance({
    requestedTab: "marketplace",
    isAuthenticated: true,
    canReadSkills: false,
    canReadMarketplace: false,
    catalogPermissionDenied: true,
  });

  assert.equal(state.hasPermission, false);
  assert.equal(state.authProjectionHasPermission, false);
  assert.equal(state.governedUnavailable, true);
});

test("keeps settings permission gaps degraded until the catalog API denies public read", () => {
  const state = resolveSkillsHubGovernance({
    requestedTab: "skills",
    isAuthenticated: true,
    canReadSkills: false,
    canReadMarketplace: false,
    projectionError: "missing_permission:settings:read",
  });

  assert.equal(state.hasPermission, true);
  assert.equal(state.governedUnavailable, false);
  assert.equal(state.degraded, true);
  assert.equal(state.pageState, "degraded");
});

test("keeps PR177 public catalogs ready when unrelated settings projection fails elsewhere", () => {
  const state = resolveSkillsHubGovernance({
    requestedTab: "marketplace",
    isAuthenticated: true,
    canReadSkills: true,
    canReadMarketplace: true,
    catalogPermissionDenied: false,
    projectionError: null,
  });

  assert.equal(state.pageState, "ready");
  assert.equal(state.hasPermission, true);
  assert.equal(state.degraded, false);
  assert.equal(state.requiredPermission, "marketplace:read");
});

test("keeps catalog permission denial authoritative over settings degradation", () => {
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
