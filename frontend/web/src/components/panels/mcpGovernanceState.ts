import type { FrontendGovernanceState } from "../governance/frontendGovernanceState";
import {
  isPermissionError,
  resolveFrontendGovernanceState,
} from "../governance/frontendGovernanceState";
import {
  resolveGroupAvailability,
  type GroupAvailabilityResult,
} from "../governance/groupAvailability";
import type { MCPServerResponse } from "../../types";

export interface McpGovernanceStateInput {
  isAuthenticated: boolean;
  isLoading?: boolean;
  hasWorkspace?: boolean;
  canReadMcp: boolean;
  servers: MCPServerResponse[];
  total: number;
  loadError?: string | null;
}

export interface McpGovernanceState {
  pageState: FrontendGovernanceState;
  requiredPermission: "mcp:read";
  authProjectionHasPermission: boolean;
  governedUnavailable: boolean;
  directoryAvailability: GroupAvailabilityResult;
  lifecycleAvailability: GroupAvailabilityResult;
  credentialsAvailability: GroupAvailabilityResult;
}

export function resolveMcpGovernanceState({
  isAuthenticated,
  isLoading = false,
  hasWorkspace = true,
  canReadMcp,
  servers,
  total,
  loadError,
}: McpGovernanceStateInput): McpGovernanceState {
  const permissionDenied = isPermissionError(loadError);
  const hasDirectoryRows = servers.length > 0 || total > 0;
  const pageState = resolveFrontendGovernanceState({
    isAuthenticated,
    isLoading,
    hasWorkspace,
    hasPermission: !permissionDenied,
    featureEnabled: hasDirectoryRows,
    projectionError: loadError,
    degraded: !canReadMcp && !permissionDenied,
  });
  const directoryAvailability = resolveGroupAvailability({
    backed: !permissionDenied,
    enabled: hasDirectoryRows,
  });
  const lifecycleAvailability = resolveGroupAvailability({ backed: false });
  const credentialsAvailability = resolveGroupAvailability({ backed: false });

  return {
    pageState,
    requiredPermission: "mcp:read",
    authProjectionHasPermission: canReadMcp,
    governedUnavailable: permissionDenied,
    directoryAvailability,
    lifecycleAvailability,
    credentialsAvailability,
  };
}
