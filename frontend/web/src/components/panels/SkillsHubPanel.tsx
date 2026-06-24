import { useCallback, useEffect, useState } from "react";
import { Package, ShoppingBag, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../../hooks/useAuth";
import { Permission } from "../../types";
import { PanelHeader } from "../common/PanelHeader";
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
    skills: { permissionDenied: false, projectionError: null },
    marketplace: { permissionDenied: false, projectionError: null },
  });
  const catalogPermissionDeniedByTab = {
    skills: catalogStateByTab.skills.permissionDenied,
    marketplace: catalogStateByTab.marketplace.permissionDenied,
  };
  const catalogProjectionErrorByTab = {
    skills: catalogStateByTab.skills.projectionError,
    marketplace: catalogStateByTab.marketplace.projectionError,
  };
  const visibleTab = requestedTab;
  const isMarketplaceView = visibleTab === "marketplace";
  const canReadSkills = hasAnyPermission([Permission.SKILL_READ]);
  const canReadMarketplace = hasAnyPermission([Permission.MARKETPLACE_READ]);
  const showTabSwitcher = true;
  const hubGovernance = resolveSkillsHubGovernance({
    requestedTab,
    isAuthenticated,
    isLoading: authLoading,
    canReadSkills,
    canReadMarketplace,
    catalogPermissionDenied: catalogPermissionDeniedByTab[requestedTab],
    projectionError: catalogProjectionErrorByTab[requestedTab],
  });
  const governanceState = hubGovernance.pageState;
  const statusCopyKey =
    governanceState === "ready"
      ? "ready"
      : governanceState === "degraded"
      ? "degraded"
      : "permissionLimited";
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
        if (
          current.permissionDenied === nextState.permissionDenied &&
          current.projectionError === nextState.projectionError
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
      className="flex h-full min-h-0 flex-col bg-[var(--theme-workbench-canvas)] text-slate-950 dark:bg-stone-950 dark:text-stone-100"
    >
      <PanelHeader
        className="skill-panel-header"
        title={isMarketplaceView ? t("marketplace.title") : t("skillsHub.title")}
        subtitle={
          isMarketplaceView ? t("marketplace.subtitle") : t("skillsHub.subtitle")
        }
        icon={
          <Sparkles size={20} className="text-stone-600 dark:text-stone-400" />
        }
        actions={
          showTabSwitcher ? (
            <div className="inline-flex rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] p-1">
              {[
                {
                  key: "skills" as const,
                  label: t("nav.skills"),
                  icon: Package,
                  path: TAB_PATHS.skills,
                },
                {
                  key: "marketplace" as const,
                  label: t("nav.marketplace"),
                  icon: ShoppingBag,
                  path: TAB_PATHS.marketplace,
                },
              ].map(({ key, label, icon: Icon, path }) => {
                const isActive = visibleTab === key;
                return (
                  <button
                    key={key}
                    type="button"
                    onClick={() => navigate(path)}
                    className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-all ${
                      isActive
                        ? "bg-[var(--theme-primary-light)] text-[var(--theme-text)] shadow-sm"
                        : "text-[var(--theme-text-secondary)] hover:text-[var(--theme-text)]"
                    }`}
                    aria-pressed={isActive}
                  >
                    <Icon size={16} />
                    <span className="hidden sm:inline">{label}</span>
                  </button>
                );
              })}
            </div>
          ) : undefined
        }
      />

      <div className="flex min-h-0 flex-1 gap-3 px-4 pb-4">
        <aside
          data-skills-catalog-sidebar
          className={`${workbenchSurface.secondaryPanel} hidden w-64 shrink-0 flex-col gap-4 p-3 lg:flex`}
        >
          <div>
            <p className={workbenchSurface.label}>
              {t("skillsHub.title")}
            </p>
            <h2 className="mt-1 text-sm font-semibold text-slate-900 dark:text-stone-100">
              {t(`skillsHub.${statusCopyKey}.title`)}
            </h2>
            <p className="mt-2 text-xs leading-5 text-slate-500 dark:text-stone-400">
              {t(`skillsHub.${statusCopyKey}.description`)}
            </p>
          </div>

          <GovernanceAvailabilityBadge
            state={permissionAvailability.state}
            labelKey={permissionAvailability.labelKey}
          />

          <nav className="grid gap-1.5">
            {[
              {
                key: "skills" as const,
                label: t("nav.skills"),
                icon: Package,
                path: TAB_PATHS.skills,
              },
              {
                key: "marketplace" as const,
                label: t("nav.marketplace"),
                icon: ShoppingBag,
                path: TAB_PATHS.marketplace,
              },
            ].map(({ key, label, icon: Icon, path }) => {
              const isActive = visibleTab === key;
              return (
                <button
                  key={key}
                  type="button"
                  onClick={() => navigate(path)}
                  className={`flex min-h-10 items-center gap-2 rounded-md px-3 text-left text-sm font-medium transition-colors ${
                    isActive
                      ? "bg-[var(--theme-primary-light)] text-[var(--theme-text)]"
                      : "text-[var(--theme-text-secondary)] hover:bg-[var(--theme-bg-sidebar)] hover:text-[var(--theme-text)]"
                  }`}
                  aria-current={isActive ? "page" : undefined}
                >
                  <Icon size={16} />
                  <span>{label}</span>
                </button>
              );
            })}
          </nav>
        </aside>

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
