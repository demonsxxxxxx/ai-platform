import { useCallback, useState } from "react";
import { useAuth } from "../../hooks/useAuth";
import { Permission } from "../../types";
import { SkillsPanel } from "./SkillsPanel";
import {
  resolveSkillsHubGovernance,
  type SkillsHubTab,
} from "./SkillsHubPanel/state";
import { buildFrontendGovernanceSmokeAttributes } from "../governance/frontendGovernanceState";
import { workbenchSurface } from "../workbench/workbenchSurface";
import { isAiAdminUser } from "./capabilityAdmin";

interface CatalogState {
  permissionDenied: boolean;
  projectionError: string | null;
  effectivePermissions: string[];
  effectivePermissionsKnown: boolean;
  readResolved: boolean;
}

export function SkillsHubPanel() {
  const {
    user,
    hasAnyPermission,
    isAuthenticated,
    isLoading: authLoading,
  } = useAuth();

  const requestedTab: SkillsHubTab = "skills";
  const [catalogState, setCatalogState] = useState<CatalogState>({
    permissionDenied: false,
    projectionError: null,
    effectivePermissions: [],
    effectivePermissionsKnown: false,
    readResolved: false,
  });
  const catalogReadPending =
    !catalogState.readResolved &&
    !catalogState.permissionDenied &&
    !catalogState.projectionError;
  const canReadSkills = hasAnyPermission([Permission.SKILL_READ]);
  const hubGovernance = resolveSkillsHubGovernance({
    requestedTab,
    isAuthenticated,
    isLoading: authLoading,
    canReadSkills,
    canReadMarketplace: false,
    catalogPermissionDenied: catalogState.permissionDenied,
    catalogReadResolved: catalogState.readResolved,
    projectionError: catalogState.projectionError,
    effectivePermissions: catalogState.effectivePermissions,
    effectivePermissionsKnown: catalogState.effectivePermissionsKnown,
    catalogReadPending,
  });
  const governanceState = hubGovernance.pageState;
  const isAdmin = isAiAdminUser(user);

  const handleCatalogStateChange = useCallback(
    (nextState: CatalogState) => {
      setCatalogState((current) => {
        const currentPermissions = current.effectivePermissions.join("\u0000");
        const nextPermissions = nextState.effectivePermissions.join("\u0000");
        if (
          current.permissionDenied === nextState.permissionDenied &&
          current.projectionError === nextState.projectionError &&
          current.readResolved === nextState.readResolved &&
          current.effectivePermissionsKnown === nextState.effectivePermissionsKnown &&
          currentPermissions === nextPermissions
        ) {
          return current;
        }
        return nextState;
      });
    },
    [],
  );

  return (
    <div
      data-phase1c-surface="skills-hub"
      data-skills-catalog-workbench
      {...buildFrontendGovernanceSmokeAttributes(governanceState)}
      data-required-permission={hubGovernance.requiredPermission}
      data-auth-projection-has-permission={hubGovernance.authProjectionHasPermission}
      data-effective-projection-has-permission={hubGovernance.effectiveProjectionHasPermission}
      data-effective-permissions-source={hubGovernance.effectivePermissionsSource}
      className={workbenchSurface.page}
    >
      <div
        className="flex min-h-0 flex-1 overflow-y-auto px-4 pb-6 pt-4 sm:px-6"
        data-primary-page-scroller
      >
        <section
          data-skills-catalog-main
          className="min-h-0 min-w-0 flex-1"
        >
          <div data-skill-catalog-shell className="min-h-0">
            <SkillsPanel
              allAuthorizedCatalog
              embedded
              governedUnavailable={isAdmin && hubGovernance.governedUnavailable}
              onCatalogStateChange={handleCatalogStateChange}
              showDistributionEditor={isAdmin}
            />
          </div>
        </section>
      </div>
    </div>
  );
}
