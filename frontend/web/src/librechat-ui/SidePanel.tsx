import { Download, FileText } from "lucide-react";
import { useTranslation } from "react-i18next";
import { formatFileSize } from "../components/documents/utils";
import { workbenchSurface } from "../components/workbench/workbenchSurface";
import type {
  SessionWorkspaceFile,
  SessionWorkspaceFilesStatus,
} from "../components/layout/AppContent/sessionWorkspaceFiles";

export interface LibreChatSidePanelProps {
  files?: SessionWorkspaceFile[];
  filesStatus?: SessionWorkspaceFilesStatus;
  onOpenFile?: (file: SessionWorkspaceFile) => void;
  onDownloadFile?: (file: SessionWorkspaceFile) => void;
}

function SessionFilesList({
  files,
  status,
  onOpen,
  onDownload,
  empty,
  loading,
  partial,
  unavailable,
  downloadLabel,
}: {
  files: SessionWorkspaceFile[];
  status: SessionWorkspaceFilesStatus;
  onOpen?: (file: SessionWorkspaceFile) => void;
  onDownload?: (file: SessionWorkspaceFile) => void;
  empty: string;
  loading: string;
  partial: string;
  unavailable: string;
  downloadLabel: string;
}) {
  if (status === "loading" || status === "idle") {
    return <p className={workbenchSurface.mutedText}>{loading}</p>;
  }
  if (status === "error") {
    return (
      <p
        role="status"
        className="text-xs leading-5 text-[var(--theme-text-secondary)]"
      >
        {unavailable}
      </p>
    );
  }
  return (
    <>
      {status === "partial" && (
        <p
          role="status"
          className="text-xs leading-5 text-[var(--theme-text-secondary)]"
        >
          {partial}
        </p>
      )}
      {files.length === 0 && status === "ready" ? (
        <p className={workbenchSurface.mutedText}>{empty}</p>
      ) : (
        <div className="space-y-1.5">
          {files.map((file) => {
            const canOpen = Boolean(file.preview_url || file.download_url);
            return (
              <div
                key={file.key}
                className="flex min-h-11 items-center gap-2 rounded-md bg-[var(--theme-bg-sidebar)] px-2 py-1.5 ring-1 ring-[var(--theme-border)]"
              >
                <FileText
                  size={15}
                  className="shrink-0 text-[var(--theme-text-tertiary)]"
                />
                <button
                  type="button"
                  className="min-w-0 flex-1 text-left disabled:cursor-default"
                  title={file.name}
                  disabled={!canOpen}
                  onClick={() => onOpen?.(file)}
                >
                  <span className="block truncate text-xs font-medium text-[var(--theme-text)]">
                    {file.name}
                  </span>
                  <span className="block text-[11px] text-[var(--theme-text-tertiary)]">
                    {formatFileSize(file.size_bytes)}
                  </span>
                </button>
                {file.download_url && (
                  <button
                    type="button"
                    className="shrink-0 rounded p-1 text-[var(--theme-text-tertiary)] hover:bg-[var(--theme-workbench-panel)] hover:text-[var(--theme-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-primary)]"
                    aria-label={`${downloadLabel} ${file.name}`}
                    title={`${downloadLabel} ${file.name}`}
                    onClick={() => onDownload?.(file)}
                  >
                    <Download size={14} />
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}

/** Renders the session-scoped file workspace from public projections. */
export function LibreChatSidePanel({
  files = [],
  filesStatus = "idle",
  onOpenFile,
  onDownloadFile,
}: LibreChatSidePanelProps) {
  const { t } = useTranslation();

  return (
    <aside
      data-librechat-side-panel
      className="flex h-full min-h-0 flex-col bg-[var(--theme-workbench-canvas)] px-4 py-3"
    >
      <section
        data-librechat-context-overview
        aria-labelledby="librechat-context-overview-label"
        className="min-h-0 flex-1 overflow-y-auto pr-1"
      >
        <h2
          id="librechat-context-overview-label"
          className={workbenchSurface.label}
        >
          {t("workbench.workspaceContext", "Workspace context")}
        </h2>
        <section
          data-librechat-context-section="files"
          aria-labelledby="librechat-session-files-label"
          className={`${workbenchSurface.compactPanel} mt-3 p-3`}
        >
          <div className="flex items-center justify-between gap-3">
            <div className="flex min-w-0 items-center gap-2">
              <span className={workbenchSurface.catalog.compactIconBox}>
                <FileText size={15} />
              </span>
              <h3
                id="librechat-session-files-label"
                className="truncate text-xs font-semibold text-[var(--theme-text)]"
              >
                {t("workbench.contextPanel.files")}
              </h3>
            </div>
            <span className={workbenchSurface.catalog.chip}>
              {filesStatus === "error"
                ? "!"
                : filesStatus === "loading" || filesStatus === "idle"
                  ? "…"
                  : files.length}
            </span>
          </div>
          <div className="mt-3">
            <SessionFilesList
              files={files}
              status={filesStatus}
              onOpen={onOpenFile}
              onDownload={onDownloadFile}
              empty={t("workbench.contextPanel.noFiles")}
              loading={t("workbench.contextPanel.filesLoading")}
              partial={t("workbench.contextPanel.filesPartial")}
              unavailable={t("workbench.contextPanel.filesUnavailable")}
              downloadLabel={t("documents.downloadFile")}
            />
          </div>
        </section>
      </section>
    </aside>
  );
}
