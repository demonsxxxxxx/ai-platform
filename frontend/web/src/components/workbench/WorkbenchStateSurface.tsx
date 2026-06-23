import { type ElementType, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  Building2,
  CheckCircle2,
  Loader2,
  LogIn,
  ShieldAlert,
  WifiOff,
  Dot,
} from "lucide-react";
import { GovernanceAvailabilityBadge } from "../governance/GovernanceAvailabilityBadge";
import type { FrontendGovernanceState } from "../governance/frontendGovernanceState";
import type { GovernanceAvailabilityState } from "../governance/groupAvailability";
import { workbenchSurface } from "./workbenchSurface";

const stateIcons: Record<FrontendGovernanceState, ElementType> = {
  "logged-out": LogIn,
  loading: Loader2,
  "no-workspace": Building2,
  forbidden: ShieldAlert,
  degraded: WifiOff,
  ready: CheckCircle2,
};

const stateCopyKeys: Record<
  FrontendGovernanceState,
  { title: string; description: string }
> = {
  "logged-out": {
    title: "workbench.states.logged-out.title",
    description: "workbench.states.logged-out.description",
  },
  loading: {
    title: "workbench.states.loading.title",
    description: "workbench.states.loading.description",
  },
  "no-workspace": {
    title: "workbench.states.no-workspace.title",
    description: "workbench.states.no-workspace.description",
  },
  forbidden: {
    title: "workbench.states.forbidden.title",
    description: "workbench.states.forbidden.description",
  },
  degraded: {
    title: "workbench.states.degraded.title",
    description: "workbench.states.degraded.description",
  },
  ready: {
    title: "workbench.states.ready.title",
    description: "workbench.states.ready.description",
  },
};

const availabilityLabelKeys: Record<GovernanceAvailabilityState, string> = {
  enabled: "governance.enabled",
  disabled: "governance.disabled",
  inherited: "governance.inherited",
  "admin-only": "governance.adminOnly",
  unavailable: "governance.unavailable",
};

export interface WorkbenchStateSurfaceProps {
  state: FrontendGovernanceState;
  title?: string;
  description?: string;
  icon?: ElementType;
  surface: string;
  actions?: ReactNode;
  details?: string[];
  capabilities?: Array<{
    title: string;
    description: string;
    state: GovernanceAvailabilityState;
    labelKey?: string;
  }>;
  className?: string;
}

export function WorkbenchStateSurface({
  state,
  title,
  description,
  icon,
  surface,
  actions,
  details,
  capabilities,
  className = "",
}: WorkbenchStateSurfaceProps) {
  const { t } = useTranslation();
  const Icon = icon ?? stateIcons[state];
  const copy = stateCopyKeys[state];
  const iconClass =
    state === "loading"
      ? "animate-spin text-slate-500 dark:text-stone-300"
      : "text-slate-500 dark:text-stone-300";

  return (
    <section
      data-workbench-state-surface
      data-frontend-governance-state={state}
      data-fail-closed-surface={surface}
      className={`${workbenchSurface.stateSurface} mx-auto w-full max-w-xl ${className}`}
    >
      <div className={workbenchSurface.stateIcon}>
        <Icon className={iconClass} size={22} strokeWidth={1.9} />
      </div>
      <h1 className="mt-4 text-base font-semibold text-slate-900 dark:text-stone-100">
        {title ?? t(copy.title)}
      </h1>
      <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-slate-600 dark:text-stone-300">
        {description ?? t(copy.description)}
      </p>
      {details && details.length > 0 ? (
        <div className="mx-auto mt-5 grid max-w-md gap-2 text-left">
          {details.map((detail) => (
            <div
              key={detail}
              data-workbench-state-detail
              className="flex items-start gap-2 rounded-lg bg-[var(--theme-bg-sidebar)] px-3 py-2 text-xs leading-5 text-slate-600 ring-1 ring-[var(--theme-border)] dark:bg-stone-950/70 dark:text-stone-300 dark:ring-stone-800"
            >
              <Dot
                size={18}
                strokeWidth={3}
                className="mt-0.5 shrink-0 text-slate-400 dark:text-stone-500"
              />
              <span>{detail}</span>
            </div>
          ))}
        </div>
      ) : null}
      {capabilities && capabilities.length > 0 ? (
        <div className="mx-auto mt-5 grid max-w-lg gap-2 text-left">
          {capabilities.map((capability) => (
            <div
              key={capability.title}
              data-workbench-state-capability
              className="flex flex-col gap-3 rounded-lg bg-[var(--theme-bg-sidebar)] px-3 py-3 text-xs leading-5 text-slate-600 ring-1 ring-[var(--theme-border)] dark:bg-stone-950/70 dark:text-stone-300 dark:ring-stone-800 sm:flex-row sm:items-start sm:justify-between"
            >
              <div className="min-w-0">
                <h2 className="text-sm font-semibold text-slate-800 dark:text-stone-100">
                  {capability.title}
                </h2>
                <p className="mt-1 leading-5 text-slate-500 dark:text-stone-400">
                  {capability.description}
                </p>
              </div>
              <div className="shrink-0">
                <GovernanceAvailabilityBadge
                  state={capability.state}
                  labelKey={
                    capability.labelKey ?? availabilityLabelKeys[capability.state]
                  }
                />
              </div>
            </div>
          ))}
        </div>
      ) : null}
      {actions && <div className="mt-4 flex justify-center">{actions}</div>}
    </section>
  );
}
