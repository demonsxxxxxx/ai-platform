import { Wrench } from "lucide-react";
import { useTranslation } from "react-i18next";

export function ProfileToolsTab() {
  const { t } = useTranslation();

  return (
    <div className="rounded-2xl border border-stone-200/60 bg-stone-50 p-4 dark:border-stone-600/40 dark:bg-stone-700/40">
      <div className="mb-2 flex items-center gap-2">
        <Wrench size={15} className="text-amber-500 dark:text-amber-400" />
        <h3 className="text-xs font-semibold uppercase tracking-wide text-stone-400 dark:text-stone-500">
          {t("profile.toolsManagement", "MCP Tools")}
        </h3>
      </div>
      <p className="text-sm leading-relaxed text-stone-500 dark:text-stone-400">
        {t(
          "phase2.profileToolsUnavailable",
          "MCP tool preferences require a governed backend contract and are not active in Phase 1.",
        )}
      </p>
    </div>
  );
}
