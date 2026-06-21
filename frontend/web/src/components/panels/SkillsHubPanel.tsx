import { useEffect } from "react";
import { Package, ShieldCheck, ShoppingBag, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router-dom";
import { useSettingsContext } from "../../contexts/SettingsContext";
import { useAuth } from "../../hooks/useAuth";
import { Permission } from "../../types";
import { PanelHeader } from "../common/PanelHeader";
import { MarketplacePanel } from "./MarketplacePanel";
import { SkillsPanel } from "./SkillsPanel";
import { resolveSkillsHubTab, type SkillsHubTab } from "./SkillsHubPanel/state";
import { GovernanceAvailabilityBadge } from "../governance/GovernanceAvailabilityBadge";
import { resolveGroupAvailability } from "../governance/groupAvailability";

const TAB_PATHS: Record<SkillsHubTab, string> = {
  skills: "/skills",
  marketplace: "/marketplace",
};

export function SkillsHubPanel() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const { hasAnyPermission } = useAuth();
  const { enableSkills } = useSettingsContext();

  const canReadSkills = hasAnyPermission([Permission.SKILL_READ]);
  const canReadMarketplace = hasAnyPermission([Permission.MARKETPLACE_READ]);
  const requestedTab: SkillsHubTab =
    location.pathname === "/marketplace" ? "marketplace" : "skills";
  const visibleTab = resolveSkillsHubTab(
    requestedTab,
    canReadSkills,
    canReadMarketplace,
  );
  const hasDiscoveryPermission = canReadSkills || canReadMarketplace;
  const showTabSwitcher = canReadSkills && canReadMarketplace;
  const departmentAvailability = resolveGroupAvailability({ backed: false });
  const permissionAvailability = resolveGroupAvailability({
    enabled: hasDiscoveryPermission,
    adminOnly: !hasDiscoveryPermission,
  });

  useEffect(() => {
    if (!visibleTab) return;
    const targetPath = TAB_PATHS[visibleTab];
    if (location.pathname !== targetPath) {
      navigate(targetPath, { replace: true });
    }
  }, [location.pathname, navigate, visibleTab]);

  if (!enableSkills) {
    return (
      <div
        data-phase1c-surface="skills-hub"
        className="flex h-full min-h-0 items-center justify-center p-6"
      >
        <section className="max-w-xl rounded-lg border border-stone-200 bg-white p-5 text-center shadow-[0_4px_12px_rgba(18,38,63,0.03)] dark:border-stone-800 dark:bg-stone-900">
          <ShieldCheck className="mx-auto text-stone-500" size={32} />
          <h2 className="mt-4 text-base font-semibold text-stone-900 dark:text-stone-100">
            {t("skillsHub.featureDisabled.title")}
          </h2>
          <p className="mt-2 text-sm leading-6 text-stone-600 dark:text-stone-300">
            {t("skillsHub.featureDisabled.description")}
          </p>
          <div className="mt-4 flex justify-center">
            <GovernanceAvailabilityBadge
              state="admin-only"
              labelKey="governance.adminOnly"
            />
          </div>
        </section>
      </div>
    );
  }

  if (!visibleTab) {
    return (
      <div
        data-phase1c-surface="skills-hub"
        className="flex h-full min-h-0 items-center justify-center p-6"
      >
        <section className="max-w-xl rounded-lg border border-stone-200 bg-white p-5 text-center shadow-[0_4px_12px_rgba(18,38,63,0.03)] dark:border-stone-800 dark:bg-stone-900">
          <ShieldCheck className="mx-auto text-stone-500" size={32} />
          <h2 className="mt-4 text-base font-semibold text-stone-900 dark:text-stone-100">
            {t("skillsHub.permissionLimited.title")}
          </h2>
          <p className="mt-2 text-sm leading-6 text-stone-600 dark:text-stone-300">
            {t("skillsHub.permissionLimited.description")}
          </p>
          <div className="mt-4 flex justify-center">
            <GovernanceAvailabilityBadge
              state={permissionAvailability.state}
              labelKey={permissionAvailability.labelKey}
            />
          </div>
        </section>
      </div>
    );
  }

  return (
    <div
      data-phase1c-surface="skills-hub"
      className="skill-theme-shell flex h-full min-h-0 flex-col"
    >
      <PanelHeader
        className="skill-panel-header"
        title={t("skillsHub.title")}
        subtitle={t("skillsHub.subtitle")}
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
        <section className="space-y-3 rounded-lg border border-stone-200/70 bg-white p-3 shadow-[0_4px_12px_rgba(18,38,63,0.03)] dark:border-stone-800 dark:bg-stone-900">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0">
              <h3 className="text-sm font-semibold text-stone-900 dark:text-stone-100">
                {t("skillsHub.permissionLimited.title")}
              </h3>
              <p className="mt-1 text-xs leading-5 text-stone-500 dark:text-stone-400">
                {t("skillsHub.permissionLimited.description")}
              </p>
            </div>
            <GovernanceAvailabilityBadge
              state={permissionAvailability.state}
              labelKey={permissionAvailability.labelKey}
            />
          </div>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0">
              <h3 className="text-sm font-semibold text-stone-900 dark:text-stone-100">
                {t("skills.marketplace.departmentAvailability")}
              </h3>
              <p className="mt-1 text-xs leading-5 text-stone-500 dark:text-stone-400">
                {t("skills.marketplace.groupToggleUnavailable")}
              </p>
            </div>
            <GovernanceAvailabilityBadge
              state={departmentAvailability.state}
              labelKey={departmentAvailability.labelKey}
            />
          </div>
        </section>
      </div>

      {/* Child panel handles its own padding via skill-panel-header + skill-content-area */}
      <div className="min-h-0 flex-1 overflow-hidden">
        {visibleTab === "skills" ? (
          <SkillsPanel embedded />
        ) : (
          <MarketplacePanel embedded />
        )}
      </div>
    </div>
  );
}
