import {
  Activity,
  Download,
  FileText,
  Package,
  Server,
  ShieldCheck,
} from "lucide-react";
import type { ComponentType, ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type {
  MessageAttachment,
  PendingApproval,
  SkillResponse,
  ToolState,
} from "../types";
import { workbenchSurface } from "../components/workbench/workbenchSurface";
import type { SessionInputFile } from "../services/api";

export type SessionFilesProjectionStatus =
  | "idle"
  | "loading"
  | "ready"
  | "error";

export interface LibreChatSidePanelProps {
  sessionId: string | null;
  currentRunId: string | null;
  messageCount: number;
  skills?: SkillResponse[];
  tools?: ToolState[];
  /** @deprecated Composer attachments are intentionally not session context. */
  attachments?: MessageAttachment[];
  sessionFiles?: SessionInputFile[];
  sessionFilesStatus?: SessionFilesProjectionStatus;
  onOpenSessionFile?: (file: SessionInputFile) => void;
  onDownloadSessionFile?: (file: SessionInputFile) => void;
  approvals?: PendingApproval[];
}

interface ContextSectionProps {
  section: "run" | "skills" | "mcp" | "files" | "permissions";
  icon: ComponentType<{ size?: number }>;
  title: string;
  count: number | string;
  children: ReactNode;
}

function ContextSection({
  section,
  icon: Icon,
  title,
  count,
  children,
}: ContextSectionProps) {
  return (
    <section
      data-librechat-context-section={section}
      className={`${workbenchSurface.compactPanel} p-3`}
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <span className={workbenchSurface.catalog.compactIconBox}>
            <Icon size={15} />
          </span>
          <h3 className="truncate text-xs font-semibold text-[var(--theme-text)]">
            {title}
          </h3>
        </div>
        <span className={workbenchSurface.catalog.chip}>{count}</span>
      </div>
      <div className="mt-3 space-y-2">{children}</div>
    </section>
  );
}

function MiniRow({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-3 text-xs">
      <dt className={workbenchSurface.mutedText}>{label}</dt>
      <dd className="max-w-36 truncate font-medium text-[var(--theme-text)]">
        {value}
      </dd>
    </div>
  );
}

function InlineList({
  items,
  empty,
}: {
  items: string[];
  empty: string;
}) {
  const visibleItems = items.slice(0, 3);
  return visibleItems.length > 0 ? (
    <div className="flex flex-wrap gap-1.5">
      {visibleItems.map((item) => (
        <span
          key={item}
          className="max-w-full truncate rounded-md bg-[var(--theme-bg-sidebar)] px-2 py-1 text-[11px] font-medium text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]"
          title={item}
        >
          {item}
        </span>
      ))}
      {items.length > visibleItems.length && (
        <span className="rounded-md bg-[var(--theme-bg-sidebar)] px-2 py-1 text-[11px] font-medium text-[var(--theme-text-tertiary)] ring-1 ring-[var(--theme-border)]">
          +{items.length - visibleItems.length}
        </span>
      )}
    </div>
  ) : (
    <p className="text-xs leading-5 text-[var(--theme-text-secondary)]">
      {empty}
    </p>
  );
}

function SessionFilesList({
  files,
  status,
  onOpen,
  onDownload,
  empty,
  loading,
  unavailable,
}: {
  files: SessionInputFile[];
  status: SessionFilesProjectionStatus;
  onOpen?: (file: SessionInputFile) => void;
  onDownload?: (file: SessionInputFile) => void;
  empty: string;
  loading: string;
  unavailable: string;
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
  if (files.length === 0) {
    return <p className={workbenchSurface.mutedText}>{empty}</p>;
  }
  return (
    <div className="space-y-1.5">
      {files.map((file) => (
        <div
          key={file.file_id}
          className="flex items-center gap-1 rounded-md bg-[var(--theme-bg-sidebar)] p-1 ring-1 ring-[var(--theme-border)]"
        >
          <button
            type="button"
            className="min-w-0 flex-1 truncate rounded px-1.5 py-1 text-left text-xs font-medium text-[var(--theme-text-secondary)] hover:text-[var(--theme-text)]"
            title={file.name}
            onClick={() => onOpen?.(file)}
          >
            {file.name}
          </button>
          <button
            type="button"
            className="rounded p-1 text-[var(--theme-text-tertiary)] hover:bg-[var(--theme-workbench-panel)] hover:text-[var(--theme-text)]"
            aria-label={`Download ${file.name}`}
            onClick={() => onDownload?.(file)}
          >
            <Download size={13} />
          </button>
        </div>
      ))}
    </div>
  );
}

/** Renders the right context panel using ai-platform-owned run and projection data. */
export function LibreChatSidePanel({
  sessionId,
  currentRunId,
  messageCount,
  skills = [],
  tools = [],
  sessionFiles = [],
  sessionFilesStatus = "idle",
  onOpenSessionFile,
  onDownloadSessionFile,
  approvals = [],
}: LibreChatSidePanelProps) {
  const { t } = useTranslation();
  const selectedSkillNames = skills
    .filter((skill) => skill.enabled)
    .map((skill) => skill.name);
  const selectedToolNames = tools
    .filter((tool) => tool.enabled)
    .map((tool) => tool.name);
  const pendingApprovals = approvals.filter(
    (approval) => approval.status === "pending",
  );
  const selectedSkillsCount = selectedSkillNames.length;
  const selectedToolsCount = selectedToolNames.length;
  const attachmentsCount = sessionFiles.length;
  const approvalCount = pendingApprovals.length;

  return (
    <aside
      data-librechat-side-panel
      className="flex h-full min-h-0 flex-col gap-3 bg-[var(--theme-workbench-canvas)] p-3"
    >
      <section
        data-librechat-context-overview
        aria-labelledby="librechat-context-overview-label"
        className={`${workbenchSurface.secondaryPanel} min-h-0 flex-1 overflow-y-auto p-3`}
      >
        <h2
          id="librechat-context-overview-label"
          className={workbenchSurface.label}
        >
          {t("workbench.workspaceContext", "Workspace context")}
        </h2>
        <div className="mt-3 space-y-3">
          <ContextSection
            section="run"
            icon={Activity}
            title={t("workbench.contextPanel.run")}
            count={messageCount}
          >
            <dl className="space-y-2">
              <MiniRow
                label={t("workbench.session", "Session")}
                value={sessionId ?? t("workbench.unsaved", "Unsaved")}
              />
              <MiniRow
                label={t("workbench.runState", "Run state")}
                value={currentRunId ?? t("workbench.noRun", "No active run")}
              />
              <MiniRow
                label={t("workbench.messages", "Messages")}
                value={messageCount}
              />
            </dl>
          </ContextSection>

          <ContextSection
            section="skills"
            icon={Package}
            title={t("workbench.contextPanel.selectedSkills")}
            count={selectedSkillsCount}
          >
            <InlineList
              items={selectedSkillNames}
              empty={t("workbench.contextPanel.noSkills")}
            />
          </ContextSection>

          <ContextSection
            section="mcp"
            icon={Server}
            title={t("workbench.contextPanel.selectedTools")}
            count={selectedToolsCount}
          >
            <InlineList
              items={selectedToolNames}
              empty={t("workbench.contextPanel.noTools")}
            />
          </ContextSection>

          <ContextSection
            section="files"
            icon={FileText}
            title={t("workbench.contextPanel.files")}
            count={
              sessionFilesStatus === "error"
                ? "!"
                : sessionFilesStatus === "ready"
                  ? attachmentsCount
                  : "…"
            }
          >
            <SessionFilesList
              files={sessionFiles}
              status={sessionFilesStatus}
              onOpen={onOpenSessionFile}
              onDownload={onDownloadSessionFile}
              empty={t("workbench.contextPanel.noFiles")}
              loading={t("workbench.contextPanel.filesLoading", {
                defaultValue: "Loading session files…",
              })}
              unavailable={t("workbench.contextPanel.filesUnavailable", {
                defaultValue: "Session files are temporarily unavailable.",
              })}
            />
          </ContextSection>

          <ContextSection
            section="permissions"
            icon={ShieldCheck}
            title={t("workbench.contextPanel.permissions")}
            count={approvalCount}
          >
            <p className="text-xs leading-5 text-[var(--theme-text-secondary)]">
              {approvalCount > 0
                ? t("workbench.contextPanel.pendingApprovals", {
                    count: approvalCount,
                  })
                : t("workbench.contextPanel.noApprovals")}
            </p>
          </ContextSection>
        </div>
      </section>
    </aside>
  );
}
