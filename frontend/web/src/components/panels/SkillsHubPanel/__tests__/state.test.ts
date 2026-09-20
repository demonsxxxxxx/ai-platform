import test from "node:test";
import assert from "node:assert/strict";

import { resolveSkillsHubGovernance } from "../state.ts";
import { resolveFrontendGovernanceState } from "../../../governance/frontendGovernanceState.ts";

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
    isAuthenticated: true,
    canReadSkills: true,
  });

  assert.equal(state.pageState, "ready");
  assert.equal(state.hasPermission, true);
  assert.equal(state.authProjectionHasPermission, true);
  assert.equal(state.effectiveProjectionHasPermission, false);
  assert.equal(state.requiredPermission, "skill:admin");
  assert.equal(state.effectivePermissionsSource, "auth");
});

test("keeps missing admin grants degraded while the catalog probe is unresolved", () => {
  const state = resolveSkillsHubGovernance({
    isAuthenticated: true,
    canReadSkills: false,
  });

  assert.equal(state.pageState, "degraded");
  assert.equal(state.hasPermission, false);
  assert.equal(state.governedUnavailable, false);
  assert.equal(state.effectivePermissionsSource, "probe");
});

test("keeps admin catalog probes loading without declaring permission", () => {
  const state = resolveSkillsHubGovernance({
    isAuthenticated: true,
    canReadSkills: false,
    catalogReadPending: true,
  });

  assert.equal(state.pageState, "loading");
  assert.equal(state.hasPermission, false);
  assert.equal(state.governedUnavailable, false);
});

test("uses backend skill admin permission after catalog load", () => {
  const state = resolveSkillsHubGovernance({
    isAuthenticated: true,
    canReadSkills: false,
    effectivePermissions: ["skill:admin"],
  });

  assert.equal(state.pageState, "ready");
  assert.equal(state.hasPermission, true);
  assert.equal(state.effectiveProjectionHasPermission, true);
  assert.equal(state.effectivePermissionsSource, "catalog");
});

test("keeps public catalog reads fail-closed for admin skill management", () => {
  const state = resolveSkillsHubGovernance({
    isAuthenticated: true,
    canReadSkills: false,
    catalogReadResolved: true,
    effectivePermissions: ["skill:read"],
    effectivePermissionsKnown: true,
  });

  assert.equal(state.pageState, "forbidden");
  assert.equal(state.hasPermission, false);
  assert.equal(state.governedUnavailable, true);
});

test("keeps lifecycle precedence explicit", () => {
  assert.equal(
    resolveSkillsHubGovernance({
      isAuthenticated: false,
      canReadSkills: true,
    }).pageState,
    "logged-out",
  );
  assert.equal(
    resolveSkillsHubGovernance({
      isAuthenticated: true,
      hasWorkspace: false,
      canReadSkills: true,
    }).pageState,
    "no-workspace",
  );
  assert.equal(
    resolveSkillsHubGovernance({
      isAuthenticated: true,
      canReadSkills: true,
      catalogPermissionDenied: true,
      projectionError: "catalog unavailable",
    }).pageState,
    "forbidden",
  );
});
