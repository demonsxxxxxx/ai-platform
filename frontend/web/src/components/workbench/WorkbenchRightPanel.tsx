import { Activity, FileText, History, ShieldCheck } from "lucide-react";
import { useTranslation } from "react-i18next";
import { workbenchSurface } from "./workbenchSurface";

export interface WorkbenchRightPanelProps {
  sessionId: string | null;
  currentRunId: string | null;
  messageCount: number;
}

export function WorkbenchRightPanel({
  sessionId,
  currentRunId,
  messageCount,
}: WorkbenchRightPanelProps) {
  const { t } = useTranslation();

  const statusItems = [
    {
      icon: Activity,
      label: t("workbench.runState", "Run state"),
      value: currentRunId
        ? t("workbench.runningOrRecent", "Active or recent run")
        : t("workbench.noRun", "No active run"),
    },
    {
      icon: FileText,
      label: t("workbench.artifacts", "Artifacts"),
      value: t("workbench.artifactsUnavailable", "Open after a governed run"),
    },
    {
      icon: ShieldCheck,
      label: t("workbench.permissions", "Permissions"),
      value: t("workbench.permissionsGoverned", "Policy checked at execution"),
    },
  ];

  return (
    <aside className="flex h-full min-h-0 flex-col gap-3 p-3">
      <section className={`${workbenchSurface.panel} p-4`}>
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className={workbenchSurface.label}>
              {t("workbench.contextLabel", "Context")}
            </p>
            <h2 className="mt-1 text-sm font-semibold text-stone-900 dark:text-stone-100">
              {t("workbench.workspaceContext", "Workspace context")}
            </h2>
          </div>
          <History size={18} className="text-stone-400" />
        </div>

        <dl className="mt-4 space-y-3">
          <div className="flex items-center justify-between gap-3 text-xs">
            <dt className={workbenchSurface.mutedText}>
              {t("workbench.session", "Session")}
            </dt>
            <dd className="max-w-36 truncate font-medium text-stone-700 dark:text-stone-200">
              {sessionId ?? t("workbench.unsaved", "Unsaved")}
            </dd>
          </div>
          <div className="flex items-center justify-between gap-3 text-xs">
            <dt className={workbenchSurface.mutedText}>
              {t("workbench.messages", "Messages")}
            </dt>
            <dd className="font-medium text-stone-700 dark:text-stone-200">
              {messageCount}
            </dd>
          </div>
        </dl>
      </section>

      <section
        className={`${workbenchSurface.secondaryPanel} flex min-h-0 flex-1 flex-col p-3`}
      >
        <p className={workbenchSurface.label}>
          {t("workbench.runSurfaces", "Run surfaces")}
        </p>
        <div className="mt-3 space-y-2">
          {statusItems.map((item) => {
            const Icon = item.icon;
            return (
              <div key={item.label} className={workbenchSurface.statusTile}>
                <div className="flex items-center gap-2">
                  <Icon
                    size={15}
                    className="text-slate-500 dark:text-stone-400"
                  />
                  <span className="text-xs font-medium text-slate-800 dark:text-stone-100">
                    {item.label}
                  </span>
                </div>
                <p className="mt-1 text-xs leading-5 text-slate-500 dark:text-stone-400">
                  {item.value}
                </p>
              </div>
            );
          })}
        </div>

        <div className={`mt-auto ${workbenchSurface.unavailable}`}>
          {t(
            "workbench.phase2Unavailable",
            "Artifacts, selected context, and run details stay read-only until your workspace enables them.",
          )}
        </div>
      </section>
    </aside>
  );
}
