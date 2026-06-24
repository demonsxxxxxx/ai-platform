import type { FrontendGovernanceState } from "../governance/frontendGovernanceState";
import {
  resolveGroupAvailability,
  type GroupAvailabilityResult,
} from "../governance/groupAvailability";

export type RoleGovernanceCapabilityKey =
  | "roleDirectory"
  | "departmentScope"
  | "requestFlow"
  | "auditTrail";

export interface RoleGovernanceStateInput {
  canManageRoles: boolean;
  roleDirectoryBacked: boolean;
}

export interface RoleGovernanceState {
  pageState: FrontendGovernanceState;
  roleDirectoryAvailability: GroupAvailabilityResult;
  adminAvailability: GroupAvailabilityResult;
  capabilities: Record<RoleGovernanceCapabilityKey, GroupAvailabilityResult>;
}

export function resolveRoleGovernanceState({
  canManageRoles,
  roleDirectoryBacked,
}: RoleGovernanceStateInput): RoleGovernanceState {
  const roleDirectoryAvailability = resolveGroupAvailability({
    backed: roleDirectoryBacked,
    enabled: roleDirectoryBacked && canManageRoles,
    adminOnly: roleDirectoryBacked && !canManageRoles,
  });
  const adminAvailability = resolveGroupAvailability({
    backed: true,
    enabled: canManageRoles,
    adminOnly: !canManageRoles,
  });
  const unavailable = resolveGroupAvailability({ backed: false });
  const pageState: FrontendGovernanceState = !roleDirectoryBacked
    ? "degraded"
    : canManageRoles
      ? "ready"
      : "forbidden";

  return {
    pageState,
    roleDirectoryAvailability,
    adminAvailability,
    capabilities: {
      roleDirectory: roleDirectoryAvailability,
      departmentScope: unavailable,
      requestFlow: unavailable,
      auditTrail: unavailable,
    },
  };
}
