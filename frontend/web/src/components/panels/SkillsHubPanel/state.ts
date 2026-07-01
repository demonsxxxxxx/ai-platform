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
  effectivePermissionsKnown?: boolean;
  catalogReadResolved?: boolean;
  catalogReadPending?: boolean;
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
  requiredPermission: "skill:admin" | "marketplace:admin";
  degraded: boolean;
}

function hasEffectiveAdminPermission(
  permissions: string[] | undefined,
  requiredPermission: "skill:admin" | "marketplace:admin",
): boolean {
  const permissionSet = new Set(permissions ?? []);
  if (permissionSet.has(requiredPermission)) {
    return true;
  }

  return (
    permissionSet.has("skill:admin") ||
    permissionSet.has("marketplace:admin")
  );
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
  effectivePermissionsKnown = false,
  catalogReadResolved,
  catalogReadPending = false,
  catalogPermissionDenied,
  projectionError,
}: SkillsHubGovernanceInput): SkillsHubGovernanceState {
  const requiredPermission =
    requestedTab === "marketplace" ? "marketplace:admin" : "skill:admin";
  const authProjectionHasPermission =
    requestedTab === "marketplace"
      ? canReadMarketplace
      : canReadSkills || canReadMarketplace;
  const effectiveProjectionHasPermission = hasEffectiveAdminPermission(
    effectivePermissions,
    requiredPermission,
  );
  const hasAdminPermission =
    authProjectionHasPermission || effectiveProjectionHasPermission;
  const resolvedByCatalog = Boolean(
    catalogReadResolved || effectiveProjectionHasPermission,
  );
  const effectivePermissionsSource = effectiveProjectionHasPermission
    ? "catalog"
    : authProjectionHasPermission && !effectivePermissionsKnown
      ? "auth"
      : catalogReadResolved || effectivePermissionsKnown
        ? "catalog"
        : authProjectionHasPermission
          ? "auth"
          : "probe";
  const governedUnavailable = Boolean(
    catalogPermissionDenied ||
      (!hasAdminPermission && (effectivePermissionsKnown || catalogReadResolved)),
  );
  const probingPermission =
    effectivePermissionsSource === "probe" &&
    !governedUnavailable &&
    !catalogReadPending;
  const pageState: FrontendGovernanceState = isLoading
    ? "loading"
    : !isAuthenticated
      ? "logged-out"
      : !hasWorkspace
        ? "no-workspace"
        : governedUnavailable
          ? "forbidden"
          : catalogReadPending
            ? "loading"
        : projectionError || probingPermission
          ? "degraded"
          : "ready";

  if (requestedTab === "marketplace") {
    return {
      pageState,
      hasPermission: hasAdminPermission && !governedUnavailable,
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
    hasPermission: hasAdminPermission && !governedUnavailable,
    authProjectionHasPermission,
    effectiveProjectionHasPermission,
    effectivePermissionsSource,
    catalogReadResolved: resolvedByCatalog,
    governedUnavailable,
    requiredPermission,
    degraded: Boolean(projectionError || probingPermission),
  };
}
