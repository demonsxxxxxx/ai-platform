import { FileStack, ListChecks, ShieldAlert, ShieldCheck } from "lucide-react";
import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { PanelHeader } from "../common/PanelHeader";
import { GovernanceAvailabilityBadge } from "../governance/GovernanceAvailabilityBadge";
import { resolveFrontendGovernanceState } from "../governance/frontendGovernanceState";
import { resolveGroupAvailability } from "../governance/groupAvailability";
import { WorkbenchStateSurface } from "../workbench/WorkbenchStateSurface";
import { workbenchSurface } from "../workbench/workbenchSurface";
import { useAuth } from "../../hooks/useAuth";
import { RevealedFilesPanel } from "./RevealedFilesPanel";

export function RevealedFilesWorkbenchPanel() {
  const { t } = useTranslation();
  const {
    isAuthenticated,
    isLoading: authLoading,
  } = useAuth();
  const [filesProjectionError, setFilesProjectionError] = useState<
    string | null
  >(null);
  const handleProjectionStateChange = useCallback(
    (error: string | null) => {
      setFilesProjectionError(error);
    },
    [],
  );
  const governanceState = resolveFrontendGovernanceState({
    isAuthenticated,
    isLoading: authLoading,
    hasPermission: true,
    projectionError: filesProjectionError,
  });
  const projectionBacked = governanceState !== "degraded";
  const readAvailability = resolveGroupAvailability({
    backed: projectionBacked,
    enabled: projectionBacked && governanceState === "ready",
  });
  const previewAvailability = resolveGroupAvailability({
    backed: projectionBacked,
    enabled: projectionBacked && governanceState === "ready",
  });
  const fileContractEndpoints = [
    "GET /api/files/revealed",
    "GET /api/files/revealed/grouped",
    "GET /api/files/revealed/stats",
    "GET /api/files/revealed/sessions",
    "PATCH /api/files/revealed/{file_id}/favorite",
  ];

  if (governanceState === "loading" || governanceState === "logged-out") {
    return (
      <div
        data-files-workbench-shell
        data-frontend-governance-state={governanceState}
        className={workbenchSurface.statePage}
      >
        <WorkbenchStateSurface
          state={governanceState}
          surface="revealed-files-workbench"
        />
      </div>
    );
  }

  if (governanceState === "degraded") {
    return (
      <div
        data-files-workbench-shell
        data-frontend-governance-state={governanceState}
        className={workbenchSurface.page}
      >
        <PanelHeader
          title={t("fileLibrary.workbenchTitle", "文件工作台")}
          subtitle={t(
            "fileLibrary.workbenchSubtitle",
            "按会话查看已揭示文件、项目产物和可预览附件。",
          )}
          icon={<FileStack size={20} className="text-slate-600" />}
          actions={
            <div className="flex flex-wrap items-center justify-end gap-1.5">
              <GovernanceAvailabilityBadge
                state={readAvailability.state}
                labelKey={readAvailability.labelKey}
              />
              <GovernanceAvailabilityBadge
                state={previewAvailability.state}
                labelKey={previewAvailability.labelKey}
              />
            </div>
          }
        />
        <div
          data-files-degraded-workbench-grid
          className="grid min-h-0 flex-1 gap-3 overflow-hidden px-4 pb-4 xl:grid-cols-[minmax(0,1fr)_18rem]"
        >
          <main data-files-degraded-main className="min-h-0 overflow-y-auto">
            <WorkbenchStateSurface
              state="degraded"
              surface="revealed-files-workbench"
              title={t("fileLibrary.degradedTitle", "文件投影已降级")}
              description={t(
                "fileLibrary.degradedDescription",
                "后端文件库投影暂不可用；页面保留工作台入口，并避免把缺路由误显示为空文件库。",
              )}
              details={[
                t(
                  "fileLibrary.backendGapDetail",
                  "后端文件库投影尚未返回可读目录；列表、分组、统计和预览操作会保持锁定，直到 /api/files/revealed 返回工作台合同。",
                ),
                t(
                  "fileLibrary.degradedSafeDetail",
                  "会话分组、项目过滤、收藏和预览控件保留在工作台语境中，但不会绕过 ACL 或展示未经授权的文件 URL。",
                ),
              ]}
              capabilities={[
                {
                  title: t("fileLibrary.readContract", "安全读取"),
                  description: t(
                    "fileLibrary.readContractDescription",
                    "文件列表使用 /api/files/revealed 公共工作台接口，预览 URL 继续执行 allowlist 校验。",
                  ),
                  state: readAvailability.state,
                  labelKey: readAvailability.labelKey,
                },
                {
                  title: t("fileLibrary.sessionGrouping", "按会话归档"),
                  description: t(
                    "fileLibrary.sessionGroupingDescription",
                    "保留原文件库的会话分组、收藏、排序、项目过滤和预览交互。",
                  ),
                  state: previewAvailability.state,
                  labelKey: previewAvailability.labelKey,
                },
              ]}
              className="max-w-none"
            />
          </main>

          <aside
            data-files-degraded-contract
            className={`${workbenchSurface.compactPanel} min-h-0 overflow-y-auto p-3`}
          >
            <div className="flex items-center gap-2">
              <div className={workbenchSurface.stateIcon}>
                <ShieldAlert size={18} />
              </div>
              <div className="min-w-0">
                <p className={workbenchSurface.label}>
                  {t("workbench.governedRoute.surfaceLabel", "受治理边界")}
                </p>
                <h2 className="mt-1 text-sm font-semibold text-slate-900 dark:text-stone-100">
                  {t(
                    "fileLibrary.contractBoundaryTitle",
                    "等待 revealed files 投影",
                  )}
                </h2>
              </div>
            </div>
            <p className="mt-3 text-xs leading-5 text-slate-500 dark:text-stone-400">
              {t(
                "fileLibrary.contractBoundaryDescription",
                "前端只展示公开文件投影；接口缺失时保持降级工作台，不把 404 当作空文件库。",
              )}
            </p>
            <div className="mt-4 space-y-2">
              {fileContractEndpoints.map((endpoint) => (
                <div
                  key={endpoint}
                  className={`${workbenchSurface.statusTile} flex items-start gap-2 text-xs leading-5 text-[var(--theme-text-secondary)]`}
                >
                  <ListChecks
                    size={15}
                    className="mt-0.5 shrink-0 text-slate-400 dark:text-stone-500"
                  />
                  <span className="break-all font-mono">{endpoint}</span>
                </div>
              ))}
            </div>
          </aside>
        </div>
      </div>
    );
  }

  return (
    <div
      data-files-workbench-shell
      data-frontend-governance-state={governanceState}
      className={workbenchSurface.page}
    >
      <PanelHeader
        title={t("fileLibrary.workbenchTitle", "文件工作台")}
        subtitle={t(
          "fileLibrary.workbenchSubtitle",
          "按会话查看已揭示文件、项目产物和可预览附件。",
        )}
        icon={<FileStack size={20} className="text-slate-600" />}
        actions={
          <div className="flex flex-wrap items-center justify-end gap-1.5">
            <GovernanceAvailabilityBadge
              state={readAvailability.state}
              labelKey={readAvailability.labelKey}
            />
            <GovernanceAvailabilityBadge
              state={previewAvailability.state}
              labelKey={previewAvailability.labelKey}
            />
          </div>
        }
      />

      <div className="grid gap-3 px-4 pb-3 lg:grid-cols-2">
        <div className={`${workbenchSurface.compactPanel} p-3`}>
          <div className="flex items-start gap-3">
            <ShieldCheck size={17} className="mt-0.5 text-slate-500" />
            <div className="min-w-0">
              <h3 className="text-sm font-semibold text-slate-900 dark:text-stone-100">
                {t("fileLibrary.readContract", "安全读取")}
              </h3>
              <p className="mt-1 text-xs leading-5 text-slate-500 dark:text-stone-400">
                {t(
                  "fileLibrary.readContractDescription",
                  "文件列表使用 /api/files/revealed 公共工作台接口，预览 URL 继续执行 allowlist 校验。",
                )}
              </p>
            </div>
          </div>
        </div>
        <div className={`${workbenchSurface.compactPanel} p-3`}>
          <div className="flex items-start gap-3">
            <FileStack size={17} className="mt-0.5 text-slate-500" />
            <div className="min-w-0">
              <h3 className="text-sm font-semibold text-slate-900 dark:text-stone-100">
                {t("fileLibrary.sessionGrouping", "按会话归档")}
              </h3>
              <p className="mt-1 text-xs leading-5 text-slate-500 dark:text-stone-400">
                {t(
                  "fileLibrary.sessionGroupingDescription",
                  "保留原文件库的会话分组、收藏、排序、项目过滤和预览交互。",
                )}
              </p>
            </div>
          </div>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-hidden">
        <RevealedFilesPanel
          onProjectionStateChange={handleProjectionStateChange}
        />
      </div>
    </div>
  );
}
