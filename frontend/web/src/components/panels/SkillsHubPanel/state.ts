export type SkillsHubTab = "skills" | "marketplace";

export interface SkillsHubGovernanceInput {
  requestedTab: SkillsHubTab;
  isAuthenticated: boolean;
  isLoading?: boolean;
  canReadSkills: boolean;
  canReadMarketplace: boolean;
  catalogPermissionDenied?: boolean;
  projectionError?: string | null;
}

export interface SkillsHubGovernanceState {
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
  canReadSkills,
  canReadMarketplace,
  catalogPermissionDenied,
  projectionError,
}: SkillsHubGovernanceInput): SkillsHubGovernanceState {
  const authProjectionHasPermission =
    requestedTab === "marketplace" ? canReadMarketplace : canReadSkills;

  if (requestedTab === "marketplace") {
    return {
      hasPermission: !catalogPermissionDenied,
      authProjectionHasPermission,
      governedUnavailable: Boolean(catalogPermissionDenied),
      requiredPermission: "marketplace:read",
      degraded: Boolean(projectionError),
    };
  }

  return {
    hasPermission: !catalogPermissionDenied,
    authProjectionHasPermission,
    governedUnavailable: Boolean(catalogPermissionDenied),
    requiredPermission: "skill:read",
    degraded: Boolean(projectionError),
  };
}
