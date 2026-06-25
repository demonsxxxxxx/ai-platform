import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../../hooks/useAuth";
import { Permission } from "../../types";
import { MarketplacePanel } from "./MarketplacePanel";
import { SkillsPanel } from "./SkillsPanel";
import {
  resolveSkillsHubGovernance,
  type SkillsHubTab,
} from "./SkillsHubPanel/state";
import { GovernanceAvailabilityBadge } from "../governance/GovernanceAvailabilityBadge";
import { resolveGroupAvailability } from "../governance/groupAvailability";
import { workbenchSurface } from "../workbench/workbenchSurface";

const TAB_PATHS: Record<SkillsHubTab, string> = {
  skills: "/skills",
  marketplace: "/marketplace",
};

interface CatalogState {
  permissionDenied: boolean;
  projectionError: string | null;
  effectivePermissions: string[];
  readResolved: boolean;
}

export function SkillsHubPanel() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const {
    hasAnyPermission,
    isAuthenticated,
    isLoading: authLoading,
  } = useAuth();

  const requestedTab: SkillsHubTab =
    location.pathname === "/marketplace" ? "marketplace" : "skills";
  const [catalogStateByTab, setCatalogStateByTab] = useState<
    Record<SkillsHubTab, CatalogState>
  >({
    skills: {
      permissionDenied: false,
      projectionError: null,
      effectivePermissions: [],
      readResolved: false,
    },
    marketplace: {
      permissionDenied: false,
      projectionError: null,
      effectivePermissions: [],
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
  const catalogReadResolvedByTab = {
    skills: catalogStateByTab.skills.readResolved,
    marketplace: catalogStateByTab.marketplace.readResolved,
  };
  const visibleTab = requestedTab;
  const isMarketplaceView = visibleTab === "marketplace";
  const canReadSkills = hasAnyPermission([Permission.SKILL_READ]);
  const canReadMarketplace = hasAnyPermission([Permission.MARKETPLACE_READ]);
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
  });
  const governanceState = hubGovernance.pageState;
  const statusCopyKey =
    governanceState === "ready"
      ? "ready"
      : governanceState === "degraded"
      ? "degraded"
      : "permissionLimited";
  const statusCopyNamespace = isMarketplaceView
    ? "skillsHub.marketplace"
    : "skillsHub.skills";
  const statusIndicatorClass =
    governanceState === "ready"
      ? "bg-emerald-500"
      : governanceState === "degraded"
        ? "bg-amber-500"
        : governanceState === "forbidden"
          ? "bg-rose-500"
          : "bg-slate-400";
  const permissionAvailability = resolveGroupAvailability({
    backed: governanceState !== "degraded",
    enabled: governanceState === "ready",
    adminOnly: governanceState === "forbidden",
  });

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
      data-frontend-governance-state={governanceState}
      data-required-permission={hubGovernance.requiredPermission}
      data-auth-projection-has-permission={hubGovernance.authProjectionHasPermission}
      data-effective-projection-has-permission={hubGovernance.effectiveProjectionHasPermission}
      data-effective-permissions-source={hubGovernance.effectivePermissionsSource}
      className={workbenchSurface.page}
    >
      <div
        data-skills-catalog-status
        className="px-4 pt-2"
      >
        <div
          data-skills-catalog-status-strip
          className="flex flex-col gap-2 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-3 py-2 shadow-[0_1px_2px_rgba(18,38,63,0.03)] sm:flex-row sm:items-center sm:justify-between"
        >
          <div className="flex min-w-0 flex-1 items-start gap-2">
            <span
              className={`mt-1 h-2 w-2 shrink-0 rounded-full ${statusIndicatorClass}`}
            />
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <h2 className="text-sm font-semibold leading-5 text-[var(--theme-text)]">
                  {t(`${statusCopyNamespace}.${statusCopyKey}.title`)}
                </h2>
                <span
                  data-skills-hub-state-detail
                  className="rounded-md bg-[var(--theme-bg-sidebar)] px-2 py-0.5 text-[11px] font-semibold text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]"
                >
                  {hubGovernance.requiredPermission}
                </span>
                <span
                  data-skills-hub-state-detail
                  className="rounded-md bg-[var(--theme-bg-sidebar)] px-2 py-0.5 text-[11px] font-medium text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]"
                >
                  {t(
                    `skillsHub.permissionSource.${hubGovernance.effectivePermissionsSource}`,
                  )}
                </span>
              </div>
              <p className="mt-0.5 line-clamp-1 text-xs leading-5 text-[var(--theme-text-secondary)]">
                {t(`${statusCopyNamespace}.${statusCopyKey}.description`)}
              </p>
            </div>
          </div>
          <GovernanceAvailabilityBadge
            state={permissionAvailability.state}
            labelKey={permissionAvailability.labelKey}
          />
        </div>
      </div>

      <div className="flex min-h-0 flex-1 px-4 pb-4 pt-2">
        <section
          data-skills-catalog-main
          className="min-h-0 min-w-0 flex-1 overflow-hidden"
        >
          {visibleTab === "skills" ? (
            <div data-skill-catalog-shell className="h-full min-h-0">
              <SkillsPanel
                embedded
                governedUnavailable={hubGovernance.governedUnavailable}
                onCatalogStateChange={handleCatalogStateChange}
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
