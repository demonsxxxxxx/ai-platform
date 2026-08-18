import { useCallback, useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../../hooks/useAuth";
import { Permission } from "../../types";
import { MarketplacePanel } from "./MarketplacePanel";
import { SkillsPanel } from "./SkillsPanel";
import {
  resolveSkillsHubGovernance,
  type SkillsHubTab,
} from "./SkillsHubPanel/state";
import { buildFrontendGovernanceSmokeAttributes } from "../governance/frontendGovernanceState";
import { workbenchSurface } from "../workbench/workbenchSurface";
import { isAiAdminUser } from "./capabilityAdmin";

const TAB_PATHS: Record<SkillsHubTab, string> = {
  skills: "/skills",
  marketplace: "/marketplace",
};

interface CatalogState {
  permissionDenied: boolean;
  projectionError: string | null;
  effectivePermissions: string[];
  effectivePermissionsKnown: boolean;
  readResolved: boolean;
}

export function SkillsHubPanel() {
  const location = useLocation();
  const navigate = useNavigate();
  const {
    user,
    hasAnyPermission,
    isAuthenticated,
    isLoading: authLoading,
  } = useAuth();

  const requestedTab: SkillsHubTab = "skills";
  const [catalogStateByTab, setCatalogStateByTab] = useState<
    Record<SkillsHubTab, CatalogState>
  >({
    skills: {
      permissionDenied: false,
      projectionError: null,
      effectivePermissions: [],
      effectivePermissionsKnown: false,
      readResolved: false,
    },
    marketplace: {
      permissionDenied: false,
      projectionError: null,
      effectivePermissions: [],
      effectivePermissionsKnown: false,
      readResolved: false,
    },
  });
  const catalogPermissionDeniedByTab = {
    skills: catalogStateByTab.skills.permissionDenied,
    marketplace: catalogStateByTab.marketplace.permissionDenied,
  };
  const catalogProjectionErrorByTab = {
    skills: catalogStateByTab.skills.projectionError,
    marketplace: catalogStateByTab.marketplace.projectionError,
  };
  const effectivePermissionsByTab = {
    skills: catalogStateByTab.skills.effectivePermissions,
    marketplace: catalogStateByTab.marketplace.effectivePermissions,
  };
  const effectivePermissionsKnownByTab = {
    skills: catalogStateByTab.skills.effectivePermissionsKnown,
    marketplace: catalogStateByTab.marketplace.effectivePermissionsKnown,
  };
  const catalogReadResolvedByTab = {
    skills: catalogStateByTab.skills.readResolved,
    marketplace: catalogStateByTab.marketplace.readResolved,
  };
  const catalogReadPendingByTab = {
    skills:
      !catalogStateByTab.skills.readResolved &&
      !catalogStateByTab.skills.permissionDenied &&
      !catalogStateByTab.skills.projectionError,
    marketplace:
      !catalogStateByTab.marketplace.readResolved &&
      !catalogStateByTab.marketplace.permissionDenied &&
      !catalogStateByTab.marketplace.projectionError,
  };
  const visibleTab = requestedTab;
  const canReadSkills = hasAnyPermission([Permission.SKILL_ADMIN]);
  const canReadMarketplace = hasAnyPermission([Permission.MARKETPLACE_ADMIN]);
  const hubGovernance = resolveSkillsHubGovernance({
    requestedTab,
    isAuthenticated,
    isLoading: authLoading,
    canReadSkills,
    canReadMarketplace,
    catalogPermissionDenied: catalogPermissionDeniedByTab[requestedTab],
    catalogReadResolved: catalogReadResolvedByTab[requestedTab],
    projectionError: catalogProjectionErrorByTab[requestedTab],
    effectivePermissions: effectivePermissionsByTab[requestedTab],
    effectivePermissionsKnown: effectivePermissionsKnownByTab[requestedTab],
    catalogReadPending: catalogReadPendingByTab[requestedTab],
  });
  const governanceState = hubGovernance.pageState;
  const isAdmin = isAiAdminUser(user);

  useEffect(() => {
    if (!visibleTab) return;
    const targetPath = TAB_PATHS[visibleTab];
    if (location.pathname !== targetPath) {
      navigate(targetPath, { replace: true });
    }
  }, [location.pathname, navigate, visibleTab]);

  const handleCatalogStateChange = useCallback(
    (nextState: CatalogState) => {
      setCatalogStateByTab((previous) => {
        const current = previous[requestedTab];
        const currentPermissions = current.effectivePermissions.join("\u0000");
        const nextPermissions = nextState.effectivePermissions.join("\u0000");
        if (
          current.permissionDenied === nextState.permissionDenied &&
          current.projectionError === nextState.projectionError &&
          current.readResolved === nextState.readResolved &&
          current.effectivePermissionsKnown === nextState.effectivePermissionsKnown &&
          currentPermissions === nextPermissions
        ) {
          return previous;
        }
        return { ...previous, [requestedTab]: nextState };
      });
    },
    [requestedTab],
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
          {visibleTab === "skills" ? (
            <div data-skill-catalog-shell className="min-h-0">
              <SkillsPanel
                allAuthorizedCatalog
                embedded
                governedUnavailable={isAdmin && hubGovernance.governedUnavailable}
                onCatalogStateChange={handleCatalogStateChange}
                showDistributionEditor={isAdmin}
              />
            </div>
          ) : (
            <div data-marketplace-catalog-shell className="h-full min-h-0">
              <MarketplacePanel
                embedded
                governedUnavailable={hubGovernance.governedUnavailable}
                onCatalogStateChange={handleCatalogStateChange}
              />
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
