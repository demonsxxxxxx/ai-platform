import type { FrontendGovernanceState } from "../../governance/frontendGovernanceState";

export type SkillsHubTab = "skills" | "marketplace";

export interface SkillsHubGovernanceInput {
  requestedTab: SkillsHubTab;
  isAuthenticated: boolean;
  isLoading?: boolean;
  hasWorkspace?: boolean;
  canReadSkills: boolean;
  canReadMarketplace: boolean;
  catalogPermissionDenied?: boolean;
  projectionError?: string | null;
}

export interface SkillsHubGovernanceState {
  pageState: FrontendGovernanceState;
  hasPermission: boolean;
  authProjectionHasPermission: boolean;
  governedUnavailable: boolean;
  requiredPermission: "skill:read" | "marketplace:read";
  degraded: boolean;
}

export function resolveSkillsHubTab(
  requestedTab: SkillsHubTab | undefined,
  canReadSkills: boolean,
  canReadMarketplace: boolean,
): SkillsHubTab | null {
  if (requestedTab) {
    return requestedTab;
  }

  if (canReadSkills && canReadMarketplace) {
    return "skills";
  }

  if (canReadSkills) {
    return "skills";
  }

  if (canReadMarketplace) {
    return "marketplace";
  }

  return "marketplace";
}

export function resolveSkillsHubGovernance({
  requestedTab,
  isAuthenticated,
  isLoading,
  hasWorkspace = true,
  canReadSkills,
  canReadMarketplace,
  catalogPermissionDenied,
  projectionError,
}: SkillsHubGovernanceInput): SkillsHubGovernanceState {
  const authProjectionHasPermission =
    requestedTab === "marketplace" ? canReadMarketplace : canReadSkills;
  const governedUnavailable = Boolean(catalogPermissionDenied);
  const pageState: FrontendGovernanceState = isLoading
    ? "loading"
    : !isAuthenticated
      ? "logged-out"
      : !hasWorkspace
        ? "no-workspace"
        : governedUnavailable
          ? "forbidden"
          : projectionError
            ? "degraded"
            : "ready";

  if (requestedTab === "marketplace") {
    return {
      pageState,
      hasPermission: !governedUnavailable,
      authProjectionHasPermission,
      governedUnavailable,
      requiredPermission: "marketplace:read",
      degraded: Boolean(projectionError),
    };
  }

  return {
    pageState,
    hasPermission: !governedUnavailable,
    authProjectionHasPermission,
    governedUnavailable,
    requiredPermission: "skill:read",
    degraded: Boolean(projectionError),
  };
}
