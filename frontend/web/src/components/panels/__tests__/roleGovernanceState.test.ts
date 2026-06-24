import test from "node:test";
import assert from "node:assert/strict";

import {
  resolveRoleGovernanceState,
  type RoleGovernanceCapabilityKey,
} from "../roleGovernanceState.ts";
import type { RoleGovernanceOverviewResponse } from "../../../types/roleGovernance.ts";

const overview: RoleGovernanceOverviewResponse = {
  governance: {
    projection: "safe_role_governance",
    tenant_id: "tenant-a",
    workspace_id: "default",
    degraded: false,
    audit_required: true,
    rollback_available: false,
    secret_material_projected: false,
  },
  role_directory: {
    roles: [
      {
        role_id: "skill_developer",
        name: "Skill Developer",
        description: "Can request Skill authoring workflows.",
        requestable: true,
        assignable: false,
        scope: "tenant",
        capabilities: ["skill_authoring"],
      },
    ],
  },
  scope: {
    tenant_id: "tenant-a",
    workspace_id: "default",
    current_department_id: "platform",
    departments: [
      {
        department_id: "platform",
        name: "platform",
        current_user_member: true,
        requestable: false,
      },
    ],
    workspaces: [
      {
        workspace_id: "default",
        name: "default",
        current: true,
        requestable: false,
      },
    ],
    skill_availability: [
      {
        skill_id: "qa-file-reviewer",
        availability_state: "inherited",
        inherited_from: "tenant",
        scope_id: "tenant-a",
      },
    ],
  },
  requests: [
    {
      request_id: "role-req-user",
      requester_id: "user",
      target_type: "role",
      target_id: "skill_developer",
      status: "pending",
      reason: "Current request",
      audit_id: null,
    },
  ],
  audit: [
    {
      audit_id: "role-governance-current",
      action: "role_governance.projection.viewed",
      target_type: "role_governance",
      target_id: "tenant-a",
      actor_id: "user",
      source: "role_governance_projection",
      status: "recorded",
      rollback_available: false,
    },
  ],
};

test("missing role governance overview stays reachable as degraded with fail-closed controls", () => {
  const state = resolveRoleGovernanceState({
    isAuthenticated: true,
    canManageRoles: false,
    canRequestRoles: false,
    overview: null,
  });

  assert.equal(state.pageState, "degraded");
  assert.equal(state.adminAvailability.state, "admin-only");
  assert.equal(state.roleDirectoryAvailability.state, "unavailable");
  assert.equal(state.capabilities.requestFlow.state, "unavailable");
});

test("ordinary request-capable users see the backed directory as ready instead of admin-only", () => {
  const state = resolveRoleGovernanceState({
    isAuthenticated: true,
    canManageRoles: false,
    canRequestRoles: true,
    overview,
  });

  assert.equal(state.pageState, "ready");
  assert.equal(state.adminAvailability.state, "admin-only");
  assert.equal(state.roleDirectoryAvailability.state, "enabled");
  assert.equal(state.capabilities.departmentScope.state, "inherited");
  assert.equal(state.capabilities.requestFlow.state, "enabled");
  assert.equal(state.capabilities.auditTrail.state, "enabled");
});

test("role managers see backed role directory and admin controls as enabled", () => {
  const state = resolveRoleGovernanceState({
    isAuthenticated: true,
    canManageRoles: true,
    canRequestRoles: true,
    overview: {
      ...overview,
      governance: {
        ...overview.governance,
        rollback_available: true,
      },
      audit: overview.audit.map((item) => ({
        ...item,
        rollback_available: true,
      })),
    },
  });

  assert.equal(state.pageState, "ready");
  assert.equal(state.adminAvailability.state, "enabled");
  assert.equal(state.roleDirectoryAvailability.state, "enabled");
  assert.equal(state.capabilities.auditTrail.state, "enabled");
});

test("permission-denied overview failures render forbidden instead of degraded", () => {
  const state = resolveRoleGovernanceState({
    isAuthenticated: true,
    canManageRoles: false,
    canRequestRoles: false,
    overview: null,
    loadError: "missing_permission:role:read",
  });

  assert.equal(state.pageState, "forbidden");
});

test("loading and logged-out role governance states stay explicit", () => {
  assert.equal(
    resolveRoleGovernanceState({
      isAuthenticated: true,
      isLoading: true,
      canManageRoles: false,
      canRequestRoles: false,
      overview: null,
    }).pageState,
    "loading",
  );
  assert.equal(
    resolveRoleGovernanceState({
      isAuthenticated: false,
      canManageRoles: false,
      canRequestRoles: false,
      overview,
    }).pageState,
    "logged-out",
  );
});

test("unbacked role governance surfaces remain visible and unavailable", () => {
  const state = resolveRoleGovernanceState({
    isAuthenticated: true,
    canManageRoles: true,
    canRequestRoles: true,
    overview: null,
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
