import { useCallback, useEffect, useState } from "react";
import { Package, ShoppingBag, Sparkles, TerminalSquare } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router-dom";
import { useSettingsContext } from "../../contexts/SettingsContext";
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

export function SkillsHubPanel() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const {
    hasAnyPermission,
    isAuthenticated,
    isLoading: authLoading,
  } = useAuth();
  const {
    isLoading: settingsLoading,
    error: settingsError,
  } = useSettingsContext();

  const requestedTab: SkillsHubTab =
    location.pathname === "/marketplace" ? "marketplace" : "skills";
  const [catalogPermissionDeniedByTab, setCatalogPermissionDeniedByTab] =
    useState<Record<SkillsHubTab, boolean>>({
      skills: false,
      marketplace: false,
    });
  const visibleTab = requestedTab;
  const isMarketplaceView = visibleTab === "marketplace";
  const canReadSkills = hasAnyPermission([Permission.SKILL_READ]);
  const canReadMarketplace = hasAnyPermission([Permission.MARKETPLACE_READ]);
  const showTabSwitcher = true;
  const skillsProjectionDegraded = Boolean(settingsError);
  const hubGovernance = resolveSkillsHubGovernance({
    requestedTab,
    isAuthenticated,
    isLoading: authLoading || settingsLoading,
    canReadSkills,
    canReadMarketplace,
    catalogPermissionDenied: catalogPermissionDeniedByTab[requestedTab],
    projectionError: settingsError,
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

  const handleCatalogPermissionDeniedChange = useCallback(
    (permissionDenied: boolean) => {
      setCatalogPermissionDeniedByTab((previous) => {
        if (previous[requestedTab] === permissionDenied) {
          return previous;
        }
        return { ...previous, [requestedTab]: permissionDenied };
      });
    },
    [requestedTab],
  );

  return (
    <div
      data-phase1c-surface="skills-hub"
      data-frontend-governance-state={governanceState}
      data-required-permission={hubGovernance.requiredPermission}
      data-auth-projection-has-permission={hubGovernance.authProjectionHasPermission}
      className="flex h-full min-h-0 flex-col bg-[var(--theme-bg)] text-slate-950 dark:bg-stone-950 dark:text-stone-100"
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

      <div className="px-4 pb-3">
        <section className="grid gap-3 lg:grid-cols-2">
          <div className={`${workbenchSurface.compactPanel} flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between`}>
            <div className="min-w-0">
              <h3 className="text-sm font-semibold text-stone-900 dark:text-stone-100">
                {t(`skillsHub.${statusCopyKey}.title`)}
              </h3>
              <p className="mt-1 text-xs leading-5 text-stone-500 dark:text-stone-400">
                {t(`skillsHub.${statusCopyKey}.description`)}
              </p>
            </div>
            <GovernanceAvailabilityBadge
              state={permissionAvailability.state}
              labelKey={permissionAvailability.labelKey}
            />
          </div>
          <div className={`${workbenchSurface.compactPanel} flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between`}>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <TerminalSquare size={16} className="text-stone-500 dark:text-stone-400" />
                <h3 className="text-sm font-semibold text-stone-900 dark:text-stone-100">
                  {t("skillsHub.composerEntry.title")}
                </h3>
              </div>
              <p className="mt-1 text-xs leading-5 text-stone-500 dark:text-stone-400">
                {t("skillsHub.composerEntry.description")}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-1 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] p-1 text-[11px] font-semibold text-stone-600 dark:border-stone-800 dark:bg-stone-950 dark:text-stone-300">
              <span className="rounded-md bg-slate-100 px-2 py-1 shadow-sm dark:bg-stone-900">
                /
              </span>
              <span className="rounded-md bg-slate-100 px-2 py-1 shadow-sm dark:bg-stone-900">
                $
              </span>
            </div>
          </div>
        </section>
      </div>

      {/* Child panel handles its own padding via skill-panel-header + skill-content-area */}
      <div className="min-h-0 flex-1 overflow-hidden">
        {visibleTab === "skills" ? (
          <div data-skill-catalog-shell className="h-full min-h-0">
            <SkillsPanel
              embedded
              governedUnavailable={hubGovernance.governedUnavailable}
              onPermissionDeniedChange={handleCatalogPermissionDeniedChange}
              settingsStateDegraded={skillsProjectionDegraded}
            />
          </div>
        ) : (
          <div data-marketplace-catalog-shell className="h-full min-h-0">
            <MarketplacePanel
              embedded
              governedUnavailable={hubGovernance.governedUnavailable}
              onPermissionDeniedChange={handleCatalogPermissionDeniedChange}
              settingsStateDegraded={skillsProjectionDegraded}
            />
          </div>
        )}
      </div>
    </div>
  );
}
