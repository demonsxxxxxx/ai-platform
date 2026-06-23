import { type ElementType, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  Building2,
  CheckCircle2,
  Loader2,
  LogIn,
  ShieldAlert,
  WifiOff,
} from "lucide-react";
import type { FrontendGovernanceState } from "../governance/frontendGovernanceState";
import { workbenchSurface } from "./workbenchSurface";

const stateIcons: Record<FrontendGovernanceState, ElementType> = {
  "logged-out": LogIn,
  loading: Loader2,
  "no-workspace": Building2,
  forbidden: ShieldAlert,
  degraded: WifiOff,
  ready: CheckCircle2,
};

export interface WorkbenchStateSurfaceProps {
  state: FrontendGovernanceState;
  title?: string;
  description?: string;
  icon?: ElementType;
  surface: string;
  actions?: ReactNode;
  className?: string;
}

export function WorkbenchStateSurface({
  state,
  title,
  description,
  icon,
  surface,
  actions,
  className = "",
}: WorkbenchStateSurfaceProps) {
  const { t } = useTranslation();
  const Icon = icon ?? stateIcons[state];
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
        {title ?? t(`frontendStates.${state}.title`)}
      </h1>
      <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-slate-600 dark:text-stone-300">
        {description ?? t(`frontendStates.${state}.description`)}
      </p>
      {actions && <div className="mt-4 flex justify-center">{actions}</div>}
    </section>
  );
}
