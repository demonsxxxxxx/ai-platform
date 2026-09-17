import type { FrontendGovernanceState } from "../../governance/frontendGovernanceState";

export interface SkillsHubGovernanceInput {
  isAuthenticated: boolean;
  isLoading?: boolean;
  hasWorkspace?: boolean;
  canReadSkills: boolean;
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
  requiredPermission: "skill:admin";
  degraded: boolean;
}

function hasEffectiveAdminPermission(permissions: string[] | undefined): boolean {
  return new Set(permissions ?? []).has("skill:admin");
}

export function resolveSkillsHubGovernance({
  isAuthenticated,
  isLoading,
  hasWorkspace = true,
  canReadSkills,
  effectivePermissions,
  effectivePermissionsKnown = false,
  catalogReadResolved,
  catalogReadPending = false,
  catalogPermissionDenied,
  projectionError,
}: SkillsHubGovernanceInput): SkillsHubGovernanceState {
  const requiredPermission = "skill:admin" as const;
  const authProjectionHasPermission = canReadSkills;
  const effectiveProjectionHasPermission =
    hasEffectiveAdminPermission(effectivePermissions);
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
