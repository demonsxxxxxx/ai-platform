import { Ban, CheckCircle2, GitBranch, Lock, MinusCircle } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { GovernanceAvailabilityState } from "./groupAvailability";

const BADGE_STYLE: Record<GovernanceAvailabilityState, string> = {
  enabled:
    "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300",
  disabled:
    "bg-slate-100 text-slate-600 dark:bg-stone-800 dark:text-stone-300",
  inherited: "bg-sky-50 text-sky-700 dark:bg-sky-950/40 dark:text-sky-300",
  "admin-only":
    "bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300",
  unavailable:
    "bg-rose-50 text-rose-700 dark:bg-rose-950/40 dark:text-rose-300",
};

const ICONS: Record<GovernanceAvailabilityState, typeof CheckCircle2> = {
  enabled: CheckCircle2,
  disabled: MinusCircle,
  inherited: GitBranch,
  "admin-only": Lock,
  unavailable: Ban,
};

export interface GovernanceAvailabilityBadgeProps {
  state: GovernanceAvailabilityState;
  labelKey: string;
}

export function GovernanceAvailabilityBadge({
  state,
  labelKey,
}: GovernanceAvailabilityBadgeProps) {
  const { t } = useTranslation();
  const Icon = ICONS[state];

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium ${BADGE_STYLE[state]}`}
      data-governance-state={state}
    >
      <Icon size={13} />
      {t(labelKey)}
    </span>
  );
}
