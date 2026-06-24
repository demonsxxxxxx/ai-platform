import type { FrontendGovernanceState } from "../governance/frontendGovernanceState";
import {
  isPermissionError,
  resolveFrontendGovernanceState,
} from "../governance/frontendGovernanceState";
import {
  resolveGroupAvailability,
  type GroupAvailabilityResult,
} from "../governance/groupAvailability";
import type { RoleGovernanceOverviewResponse } from "../../types/roleGovernance";

export type RoleGovernanceCapabilityKey =
  | "roleDirectory"
  | "departmentScope"
  | "requestFlow"
  | "auditTrail";

export interface RoleGovernanceStateInput {
  isAuthenticated: boolean;
  isLoading?: boolean;
  canManageRoles: boolean;
  canRequestRoles: boolean;
  overview: RoleGovernanceOverviewResponse | null;
  loadError?: string | null;
}

export interface RoleGovernanceState {
  pageState: FrontendGovernanceState;
  roleDirectoryAvailability: GroupAvailabilityResult;
  adminAvailability: GroupAvailabilityResult;
  capabilities: Record<RoleGovernanceCapabilityKey, GroupAvailabilityResult>;
}

export function resolveRoleGovernanceState({
  isAuthenticated,
  isLoading = false,
  canManageRoles,
  canRequestRoles,
  overview,
  loadError,
}: RoleGovernanceStateInput): RoleGovernanceState {
  const roleDirectoryBacked = Boolean(overview);
  const hasRoles = Boolean(overview?.role_directory.roles.length);
  const hasScope = Boolean(
    overview &&
      (overview.scope.departments.length > 0 ||
        overview.scope.workspaces.length > 0 ||
        overview.scope.skill_availability.length > 0),
  );
  const hasAudit = Boolean(
    overview && (overview.governance.audit_required || overview.audit.length > 0),
  );
  const roleDirectoryAvailability = resolveGroupAvailability({
    backed: roleDirectoryBacked,
    enabled: roleDirectoryBacked && hasRoles,
  });
  const adminAvailability = resolveGroupAvailability({
    backed: true,
    enabled: canManageRoles,
    adminOnly: !canManageRoles,
  });
  const pageState: FrontendGovernanceState = resolveFrontendGovernanceState({
    isAuthenticated,
    isLoading,
    hasWorkspace: overview
      ? Boolean(overview.scope.workspace_id || overview.governance.workspace_id)
      : true,
    hasPermission: !isPermissionError(loadError),
    featureEnabled: roleDirectoryBacked,
    projectionError: loadError,
    degraded: Boolean(
      overview?.governance.degraded ||
        overview?.governance.secret_material_projected,
    ),
  });

  return {
    pageState,
    roleDirectoryAvailability,
    adminAvailability,
    capabilities: {
      roleDirectory: roleDirectoryAvailability,
      departmentScope: resolveGroupAvailability({
        backed: roleDirectoryBacked,
        inherited: hasScope,
        enabled: roleDirectoryBacked && !hasScope,
      }),
      requestFlow: resolveGroupAvailability({
        backed: roleDirectoryBacked,
        enabled: roleDirectoryBacked && canRequestRoles,
      }),
      auditTrail: resolveGroupAvailability({
        backed: roleDirectoryBacked,
        enabled: hasAudit,
      }),
    },
  };
}
