import test from "node:test";
import assert from "node:assert/strict";

import {
  FRONTEND_GOVERNANCE_SMOKE_STATES,
  buildFrontendGovernanceSmokeAttributes,
  getFrontendGovernanceStateAssertSelector,
  isPermissionError,
  resolveFrontendGovernanceState,
} from "../frontendGovernanceState.ts";

test("frontend governance state machine covers logged-out", () => {
  assert.equal(
    resolveFrontendGovernanceState({ isAuthenticated: false }),
    "logged-out",
  );
});

test("frontend governance state machine covers loading before auth decisions", () => {
  assert.equal(
    resolveFrontendGovernanceState({
      isAuthenticated: false,
      isLoading: true,
    }),
    "loading",
  );
});

test("frontend governance state machine covers no workspace", () => {
  assert.equal(
    resolveFrontendGovernanceState({
      isAuthenticated: true,
      hasWorkspace: false,
    }),
    "no-workspace",
  );
});

test("frontend governance state machine covers forbidden permissions", () => {
  assert.equal(
    resolveFrontendGovernanceState({
      isAuthenticated: true,
      hasPermission: false,
    }),
    "forbidden",
  );
  assert.equal(isPermissionError("missing_permission:skill:write"), true);
});

test("frontend governance state machine covers degraded projections", () => {
  assert.equal(
    resolveFrontendGovernanceState({
      isAuthenticated: true,
      featureEnabled: false,
    }),
    "degraded",
  );
  assert.equal(
    resolveFrontendGovernanceState({
      isAuthenticated: true,
      projectionError: "settings projection unavailable",
    }),
    "degraded",
  );
});

test("frontend governance state machine covers ready", () => {
  assert.equal(
    resolveFrontendGovernanceState({
      isAuthenticated: true,
      hasWorkspace: true,
      hasPermission: true,
    }),
    "ready",
  );
});

test("frontend governance state machine exposes a browser smoke contract for every state", () => {
  assert.deepEqual(FRONTEND_GOVERNANCE_SMOKE_STATES, [
    "logged-out",
    "loading",
    "no-workspace",
    "forbidden",
    "degraded",
    "ready",
  ]);

  for (const state of FRONTEND_GOVERNANCE_SMOKE_STATES) {
    assert.deepEqual(buildFrontendGovernanceSmokeAttributes(state), {
      "data-frontend-governance-state": state,
      "data-frontend-governance-smoke": `frontend-governance:${state}`,
    });
    assert.equal(
      getFrontendGovernanceStateAssertSelector(state),
      `[data-frontend-governance-state="${state}"][data-frontend-governance-smoke="frontend-governance:${state}"]`,
    );
  }
});
