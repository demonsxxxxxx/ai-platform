import { Building2, Lock } from "lucide-react";
import { useTranslation } from "react-i18next";
import { GovernanceAvailabilityBadge } from "./GovernanceAvailabilityBadge";
import { resolveGroupAvailability } from "./groupAvailability";

export type GroupAvailabilityToggleState =
  | "enabled"
  | "disabled"
  | "inherited"
  | "unavailable";

export interface GroupAvailabilityToggleRowProps {
  label: string;
  description: string;
  state: GroupAvailabilityToggleState;
  backed: boolean;
}

export function GroupAvailabilityToggleRow({
  label,
  description,
  state,
  backed,
}: GroupAvailabilityToggleRowProps) {
  const { t } = useTranslation();
  const availability = resolveGroupAvailability({
    backed,
    enabled: state === "enabled",
    inherited: state === "inherited",
  });
  const disabled = !backed || state === "unavailable";

  return (
    <div
      data-group-toggle-ui
      data-fail-closed-surface="department-skill-policy"
      className="flex items-start justify-between gap-3 rounded-lg border border-slate-200 bg-white p-3 shadow-[0_4px_12px_rgba(18,38,63,0.03)] dark:border-stone-800 dark:bg-stone-900"
    >
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <Building2 size={16} className="text-slate-500 dark:text-stone-400" />
          <h3 className="text-sm font-semibold text-slate-900 dark:text-stone-100">
            {label}
          </h3>
        </div>
        <p className="mt-1 text-xs leading-5 text-slate-500 dark:text-stone-400">
          {description}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <GovernanceAvailabilityBadge
          state={availability.state}
          labelKey={availability.labelKey}
        />
        <button
          type="button"
          disabled={disabled}
          aria-disabled={disabled}
          className="inline-flex h-8 min-w-16 items-center justify-center gap-1 rounded-md border border-slate-200 px-2 text-xs font-medium text-slate-500 disabled:cursor-not-allowed disabled:opacity-50 dark:border-stone-700 dark:text-stone-400"
          title={
            backed
              ? t("governance.toggleBacked")
              : t("skills.marketplace.groupToggleUnavailable")
          }
        >
          {!backed && <Lock size={12} />}
          {t(`governance.${state}`)}
        </button>
      </div>
    </div>
  );
}
