export type FrontendGovernanceState =
  | "logged-out"
  | "loading"
  | "no-workspace"
  | "forbidden"
  | "degraded"
  | "ready";

export const FRONTEND_GOVERNANCE_SMOKE_STATES = [
  "logged-out",
  "loading",
  "no-workspace",
  "forbidden",
  "degraded",
  "ready",
] as const satisfies readonly FrontendGovernanceState[];

export type FrontendGovernanceSmokeAttributes = {
  "data-frontend-governance-state": FrontendGovernanceState;
  "data-frontend-governance-smoke": `frontend-governance:${FrontendGovernanceState}`;
};

export interface FrontendGovernanceStateInput {
  isAuthenticated: boolean;
  isLoading?: boolean;
  hasWorkspace?: boolean;
  hasPermission?: boolean;
  featureEnabled?: boolean;
  projectionError?: string | null;
  degraded?: boolean;
}

/**
 * Detect backend permission projection failures before rendering governed UI.
 */
export function isPermissionError(message: string | null | undefined): boolean {
  if (!message) return false;
  return (
    message.startsWith("missing_permission:") ||
    message.includes("Missing permission:") ||
    message.includes("backendErrors.permissionMissing") ||
    message.includes("缺少权限")
  );
}

/**
 * Resolve the top-level frontend governance state used by workbench panels.
 */
export function resolveFrontendGovernanceState({
  isAuthenticated,
  isLoading = false,
  hasWorkspace = true,
  hasPermission = true,
  featureEnabled = true,
  projectionError,
  degraded = false,
}: FrontendGovernanceStateInput): FrontendGovernanceState {
  if (isLoading) return "loading";
  if (!isAuthenticated) return "logged-out";
  if (!hasWorkspace) return "no-workspace";
  if (!hasPermission || isPermissionError(projectionError)) return "forbidden";
  if (featureEnabled === false || degraded || projectionError) {
    return "degraded";
  }
  return "ready";
}

export function buildFrontendGovernanceSmokeAttributes(
  state: FrontendGovernanceState,
): FrontendGovernanceSmokeAttributes {
  return {
    "data-frontend-governance-state": state,
    "data-frontend-governance-smoke": `frontend-governance:${state}`,
  };
}

export function getFrontendGovernanceStateAssertSelector(
  state: FrontendGovernanceState,
): string {
  return `[data-frontend-governance-state="${state}"][data-frontend-governance-smoke="frontend-governance:${state}"]`;
}
