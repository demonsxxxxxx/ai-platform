import { type ElementType } from "react";
import {
  Building2,
  BellRing,
  CheckCircle2,
  ClipboardList,
  Loader2,
  LogIn,
  Settings2,
  ShieldAlert,
  ShieldCheck,
  UsersRound,
  WifiOff,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { PanelHeader } from "../common/PanelHeader";
import { GovernanceAvailabilityBadge } from "../governance/GovernanceAvailabilityBadge";
import type { GovernanceAvailabilityState } from "../governance/groupAvailability";
import type { FrontendGovernanceState } from "../governance/frontendGovernanceState";
import type { TabType } from "../layout/AppContent/types";
import { workbenchSurface } from "./workbenchSurface";

const routeIcons: Partial<Record<TabType, ElementType>> = {
  users: UsersRound,
  settings: Settings2,
  feedback: ClipboardList,
  notifications: BellRing,
};

const availabilityLabelKeys: Record<GovernanceAvailabilityState, string> = {
  enabled: "governance.enabled",
  disabled: "governance.disabled",
  inherited: "governance.inherited",
  "admin-only": "governance.adminOnly",
  unavailable: "governance.unavailable",
};

const stateIcons: Record<FrontendGovernanceState, ElementType> = {
  "logged-out": LogIn,
  loading: Loader2,
  "no-workspace": Building2,
  forbidden: ShieldAlert,
  degraded: WifiOff,
  ready: CheckCircle2,
};

const stateLabelKeys: Record<FrontendGovernanceState, string> = {
  "logged-out": "workbench.states.logged-out.title",
  loading: "workbench.states.loading.title",
  "no-workspace": "workbench.states.no-workspace.title",
  forbidden: "workbench.states.forbidden.title",
  degraded: "workbench.states.degraded.title",
  ready: "workbench.states.ready.title",
};

const stateTone: Record<FrontendGovernanceState, string> = {
  "logged-out": "bg-slate-100 text-slate-700 dark:bg-stone-800 dark:text-stone-300",
  loading: "bg-sky-50 text-sky-700 dark:bg-sky-950/40 dark:text-sky-300",
  "no-workspace": "bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300",
  forbidden: "bg-rose-50 text-rose-700 dark:bg-rose-950/40 dark:text-rose-300",
  degraded: "bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300",
  ready: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300",
};

interface GovernedRouteCapability {
  title: string;
  description: string;
  state: GovernanceAvailabilityState;
  labelKey?: string;
}

export interface GovernedRouteWorkbenchConfig {
  state: FrontendGovernanceState;
  title: string;
  description: string;
  surface: string;
  details?: string[];
  capabilities?: GovernedRouteCapability[];
}

export function GovernedRouteWorkbench({
  activeTab,
  config,
}: {
  activeTab: Exclude<TabType, "chat">;
  config: GovernedRouteWorkbenchConfig;
}) {
  const { t } = useTranslation();
  const Icon = routeIcons[activeTab] ?? ShieldCheck;
  const StateIcon = stateIcons[config.state];
  const capabilities = config.capabilities ?? [];
  const details = config.details ?? [];

  return (
    <div
      data-governed-route-workbench
      data-frontend-governance-state={config.state}
      data-fail-closed-surface={config.surface}
      className="flex h-full min-h-0 flex-col bg-[var(--theme-workbench-canvas)] text-slate-950 dark:bg-stone-950 dark:text-stone-100"
    >
      <PanelHeader
        title={config.title}
        subtitle={config.description}
        icon={<Icon size={20} className="text-theme-text-secondary" />}
      />

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        <section
          data-governed-route-summary
          className={`${workbenchSurface.panel} overflow-hidden`}
        >
          <div className="grid gap-0 xl:grid-cols-[minmax(0,1fr)_24rem]">
            <div className="p-4">
              <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                <div className="flex min-w-0 gap-3">
                  <div className={workbenchSurface.stateIcon}>
                    <StateIcon
                      size={22}
                      strokeWidth={1.9}
                      className={
                        config.state === "loading"
                          ? "animate-spin text-slate-500 dark:text-stone-300"
                          : "text-slate-500 dark:text-stone-300"
                      }
                    />
                  </div>
                  <div className="min-w-0">
                    <p className={workbenchSurface.label}>
                      {t("workbench.governedRoute.stateLabel")}
                    </p>
                    <h2 className="mt-1 text-base font-semibold text-stone-900 dark:text-stone-100">
                      {config.title}
                    </h2>
                    <p className="mt-2 max-w-3xl text-sm leading-6 text-stone-600 dark:text-stone-300">
                      {config.description}
                    </p>
                  </div>
                </div>
                <span
                  className={`inline-flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-semibold ${stateTone[config.state]}`}
                >
                  <StateIcon size={13} />
                  {t(stateLabelKeys[config.state])}
                </span>
              </div>

              <div
                data-fail-closed-surface={config.surface}
                className="mt-4 flex flex-wrap items-center gap-2 text-xs text-stone-500 dark:text-stone-400"
              >
                <span className={workbenchSurface.label}>
                  {t("workbench.governedRoute.surfaceLabel")}
                </span>
                <code className="rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] px-2 py-1 font-mono text-[11px] text-stone-600 dark:border-stone-800 dark:bg-stone-950 dark:text-stone-300">
                  {config.surface}
                </code>
              </div>
            </div>

            <div
              data-governed-route-contract
              className="border-t border-[var(--theme-border)] p-4 dark:border-stone-800 xl:border-l xl:border-t-0"
            >
              <div className="flex items-center gap-2">
                <Icon size={16} className="text-stone-500" />
                <h2 className="text-sm font-semibold text-stone-900 dark:text-stone-100">
                  {t("workbench.governedRoute.contractTitle")}
                </h2>
              </div>
              <p className="mt-1 text-xs leading-5 text-stone-500 dark:text-stone-400">
                {t("workbench.governedRoute.contractDescription")}
              </p>

              <div className="mt-4 divide-y divide-[var(--theme-border)] dark:divide-stone-800">
                {details.map((detail) => (
                  <p
                    key={detail}
                    data-governed-route-detail
                    className="py-2 text-xs leading-5 text-stone-600 first:pt-0 last:pb-0 dark:text-stone-300"
                  >
                    {detail}
                  </p>
                ))}
              </div>
            </div>
          </div>
        </section>

        {capabilities.length > 0 ? (
          <section className="mt-3 grid gap-3 lg:grid-cols-3">
            {capabilities.map((capability) => (
              <article
                key={capability.title}
                data-governed-route-capability
                className={workbenchSurface.compactPanel}
              >
                <div className="flex items-start justify-between gap-3 p-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <ShieldCheck size={16} className="text-stone-500" />
                      <h2 className="text-sm font-semibold text-stone-900 dark:text-stone-100">
                        {capability.title}
                      </h2>
                    </div>
                    <p className="mt-1 text-xs leading-5 text-stone-500 dark:text-stone-400">
                      {capability.description}
                    </p>
                  </div>
                  <div className="shrink-0">
                    <GovernanceAvailabilityBadge
                      state={capability.state}
                      labelKey={
                        capability.labelKey ??
                        availabilityLabelKeys[capability.state]
                      }
                    />
                  </div>
                </div>
              </article>
            ))}
          </section>
        ) : null}

        {details.length === 0 && capabilities.length === 0 ? (
          <section className="mt-3">
            <div className={workbenchSurface.unavailable}>
              {t("workbench.governedRoute.empty")}
            </div>
          </section>
        ) : null}
      </div>
    </div>
  );
}
