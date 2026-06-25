import type { FrontendGovernanceState } from "../../governance/frontendGovernanceState";

export type SkillsHubTab = "skills" | "marketplace";

export interface SkillsHubGovernanceInput {
  requestedTab: SkillsHubTab;
  isAuthenticated: boolean;
  isLoading?: boolean;
  hasWorkspace?: boolean;
  canReadSkills: boolean;
  canReadMarketplace: boolean;
  effectivePermissions?: string[];
  catalogReadResolved?: boolean;
  catalogPermissionDenied?: boolean;
  projectionError?: string | null;
}

export interface SkillsHubGovernanceState {
  pageState: FrontendGovernanceState;
  hasPermission: boolean;
  authProjectionHasPermission: boolean;
  effectiveProjectionHasPermission: boolean;
  effectivePermissionsSource: "catalog" | "auth" | "probe";
  catalogReadResolved: boolean;
  governedUnavailable: boolean;
  requiredPermission: "skill:read" | "marketplace:read";
  degraded: boolean;
}

function hasEffectiveReadPermission(
  permissions: string[] | undefined,
  requiredPermission: "skill:read" | "marketplace:read",
): boolean {
  const permissionSet = new Set(permissions ?? []);
  if (permissionSet.has(requiredPermission)) {
    return true;
  }

  if (requiredPermission === "skill:read") {
    return permissionSet.has("skill:admin");
  }

  return permissionSet.has("marketplace:admin");
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
  effectivePermissions,
  catalogReadResolved,
  catalogPermissionDenied,
  projectionError,
}: SkillsHubGovernanceInput): SkillsHubGovernanceState {
  const requiredPermission =
    requestedTab === "marketplace" ? "marketplace:read" : "skill:read";
  const authProjectionHasPermission =
    requestedTab === "marketplace" ? canReadMarketplace : canReadSkills;
  const effectiveProjectionHasPermission = hasEffectiveReadPermission(
    effectivePermissions,
    requiredPermission,
  );
  const resolvedByCatalog = Boolean(
    catalogReadResolved || effectiveProjectionHasPermission,
  );
  const effectivePermissionsSource = effectiveProjectionHasPermission
    ? "catalog"
    : catalogReadResolved
      ? "catalog"
    : authProjectionHasPermission
      ? "auth"
      : "probe";
  const governedUnavailable = Boolean(catalogPermissionDenied);
  const probingPermission =
    effectivePermissionsSource === "probe" && !governedUnavailable;
  const pageState: FrontendGovernanceState = isLoading
    ? "loading"
    : !isAuthenticated
      ? "logged-out"
      : !hasWorkspace
        ? "no-workspace"
        : governedUnavailable
          ? "forbidden"
          : projectionError || probingPermission
            ? "degraded"
            : "ready";

  if (requestedTab === "marketplace") {
    return {
      pageState,
      hasPermission: !governedUnavailable,
      authProjectionHasPermission,
      effectiveProjectionHasPermission,
      effectivePermissionsSource,
      catalogReadResolved: resolvedByCatalog,
      governedUnavailable,
      requiredPermission,
      degraded: Boolean(projectionError || probingPermission),
    };
  }

  return {
    pageState,
    hasPermission: !governedUnavailable,
    authProjectionHasPermission,
    effectiveProjectionHasPermission,
    effectivePermissionsSource,
    catalogReadResolved: resolvedByCatalog,
    governedUnavailable,
    requiredPermission,
    degraded: Boolean(projectionError || probingPermission),
  };
}
