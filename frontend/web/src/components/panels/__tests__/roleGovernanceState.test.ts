import test from "node:test";
import assert from "node:assert/strict";

import {
  resolveRoleGovernanceState,
  type RoleGovernanceCapabilityKey,
} from "../roleGovernanceState.ts";

test("unbacked role directory stays reachable as degraded with fail-closed controls", () => {
  const state = resolveRoleGovernanceState({
    canManageRoles: false,
    roleDirectoryBacked: false,
  });

  assert.equal(state.pageState, "degraded");
  assert.equal(state.adminAvailability.state, "admin-only");
  assert.equal(state.roleDirectoryAvailability.state, "unavailable");
  assert.equal(state.capabilities.requestFlow.state, "unavailable");
});

test("ordinary users are forbidden only when backed role management is admin-only", () => {
  const state = resolveRoleGovernanceState({
    canManageRoles: false,
    roleDirectoryBacked: true,
  });

  assert.equal(state.pageState, "forbidden");
  assert.equal(state.adminAvailability.state, "admin-only");
  assert.equal(state.roleDirectoryAvailability.state, "admin-only");
});

test("role managers see backed role directory and admin controls as enabled", () => {
  const state = resolveRoleGovernanceState({
    canManageRoles: true,
    roleDirectoryBacked: true,
  });

  assert.equal(state.pageState, "ready");
  assert.equal(state.adminAvailability.state, "enabled");
  assert.equal(state.roleDirectoryAvailability.state, "enabled");
});

test("unbacked role governance surfaces remain visible and unavailable", () => {
  const state = resolveRoleGovernanceState({
    canManageRoles: true,
    roleDirectoryBacked: false,
  });
  const capabilityKeys: RoleGovernanceCapabilityKey[] = [
    "departmentScope",
    "requestFlow",
    "auditTrail",
  ];

  for (const key of capabilityKeys) {
    assert.equal(state.capabilities[key].state, "unavailable");
  }
});
