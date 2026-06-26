import { Ban, CheckCircle2, GitBranch, Lock, MinusCircle } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { GovernanceAvailabilityState } from "./groupAvailability";

const BADGE_STYLE: Record<GovernanceAvailabilityState, string> = {
  enabled:
    "bg-[var(--theme-success-soft)] text-[var(--theme-success)] ring-[var(--theme-success-ring)]",
  disabled:
    "bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)] ring-[var(--theme-border)]",
  inherited:
    "bg-[var(--theme-info-soft)] text-[var(--theme-info)] ring-[var(--theme-info-ring)]",
  "admin-only":
    "bg-[var(--theme-warning-soft)] text-[var(--theme-warning)] ring-[var(--theme-warning-ring)]",
  unavailable:
    "bg-[var(--theme-danger-soft)] text-[var(--theme-danger)] ring-[var(--theme-danger-ring)]",
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
      className={`inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium ring-1 ${BADGE_STYLE[state]}`}
      data-governance-state={state}
    >
      <Icon size={13} />
      {t(labelKey)}
    </span>
  );
}
