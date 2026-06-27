import { Activity, FileText, History, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { workbenchSurface } from "../workbench/workbenchSurface";

export interface LibreChatSidePanelProps {
  sessionId: string | null;
  currentRunId: string | null;
  messageCount: number;
}

type LibreChatSideTab = "context" | "artifacts" | "run" | "permissions";

export function LibreChatSidePanel({
  sessionId,
  currentRunId,
  messageCount,
}: LibreChatSidePanelProps) {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<LibreChatSideTab>("context");
  const tabClassName =
    "flex h-9 items-center justify-center rounded-md text-[var(--theme-text-secondary)] transition-colors data-[active=true]:bg-[var(--theme-bg-sidebar)] data-[active=true]:text-[var(--theme-text)]";

  return (
    <aside
      data-librechat-side-panel
      className="flex h-full min-h-0 flex-col gap-3 bg-[var(--theme-workbench-canvas)] p-3"
    >
      <div className={`${workbenchSurface.secondaryPanel} p-2`}>
        <div
          className="grid grid-cols-4 gap-1"
          role="tablist"
          aria-label={t("workbench.runSurfaces", "Run surfaces")}
        >
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "context"}
            data-librechat-side-tab="context"
            data-active={activeTab === "context" ? "true" : "false"}
            className={tabClassName}
            onClick={() => setActiveTab("context")}
            title={t("workbench.contextLabel")}
          >
            <History size={15} />
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "artifacts"}
            data-librechat-side-tab="artifacts"
            data-active={activeTab === "artifacts" ? "true" : "false"}
            className={tabClassName}
            onClick={() => setActiveTab("artifacts")}
            title={t("workbench.artifacts")}
          >
            <FileText size={15} />
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "run"}
            data-librechat-side-tab="run"
            data-active={activeTab === "run" ? "true" : "false"}
            className={tabClassName}
            onClick={() => setActiveTab("run")}
            title={t("workbench.runState")}
          >
            <Activity size={15} />
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "permissions"}
            data-librechat-side-tab="permissions"
            data-active={activeTab === "permissions" ? "true" : "false"}
            className={tabClassName}
            onClick={() => setActiveTab("permissions")}
            title={t("workbench.permissions")}
          >
            <ShieldCheck size={15} />
          </button>
        </div>
      </div>

      <section
        className={`${workbenchSurface.secondaryPanel} flex min-h-0 flex-1 flex-col p-4`}
      >
        <p className={workbenchSurface.label}>
          {t("workbench.workspaceContext", "Workspace context")}
        </p>
        <dl className="mt-4 space-y-3">
          <div className="flex items-center justify-between gap-3 text-xs">
            <dt className={workbenchSurface.mutedText}>
              {t("workbench.session", "Session")}
            </dt>
            <dd className="max-w-36 truncate font-medium text-[var(--theme-text)]">
              {sessionId ?? t("workbench.unsaved", "Unsaved")}
            </dd>
          </div>
          <div className="flex items-center justify-between gap-3 text-xs">
            <dt className={workbenchSurface.mutedText}>
              {t("workbench.messages", "Messages")}
            </dt>
            <dd className="font-medium text-[var(--theme-text)]">
              {messageCount}
            </dd>
          </div>
          <div className="flex items-center justify-between gap-3 text-xs">
            <dt className={workbenchSurface.mutedText}>
              {t("workbench.runState", "Run state")}
            </dt>
            <dd className="max-w-36 truncate font-medium text-[var(--theme-text)]">
              {currentRunId ?? t("workbench.noRun", "No active run")}
            </dd>
          </div>
        </dl>
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
