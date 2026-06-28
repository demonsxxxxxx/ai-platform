import {
  Activity,
  FileText,
  History,
  Package,
  Server,
  ShieldCheck,
} from "lucide-react";
import type { ComponentType, ReactNode } from "react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type {
  MessageAttachment,
  PendingApproval,
  SkillResponse,
  ToolState,
} from "../../types";
import { workbenchSurface } from "../workbench/workbenchSurface";

export interface LibreChatSidePanelProps {
  sessionId: string | null;
  currentRunId: string | null;
  messageCount: number;
  skills?: SkillResponse[];
  tools?: ToolState[];
  attachments?: MessageAttachment[];
  approvals?: PendingApproval[];
}

type LibreChatSideTab = "context" | "artifacts" | "run" | "permissions";

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

export function LibreChatSidePanel({
  sessionId,
  currentRunId,
  messageCount,
  skills = [],
  tools = [],
  attachments = [],
  approvals = [],
}: LibreChatSidePanelProps) {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<LibreChatSideTab>("context");
  const tabClassName =
    "flex h-9 items-center justify-center rounded-md text-[var(--theme-text-secondary)] transition-colors data-[active=true]:bg-[var(--theme-bg-sidebar)] data-[active=true]:text-[var(--theme-text)]";
  const selectedSkillNames = skills
    .filter((skill) => skill.enabled)
    .map((skill) => skill.name);
  const selectedToolNames = tools
    .filter((tool) => tool.enabled)
    .map((tool) => tool.name);
  const attachmentNames = attachments.map((attachment) => attachment.name);
  const pendingApprovals = approvals.filter(
    (approval) => approval.status === "pending",
  );
  const selectedSkillsCount = selectedSkillNames.length;
  const selectedToolsCount = selectedToolNames.length;
  const attachmentsCount = attachments.length;
  const approvalCount = pendingApprovals.length;

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
        data-librechat-context-overview
        className={`${workbenchSurface.secondaryPanel} min-h-0 flex-1 overflow-y-auto p-3`}
      >
        <p className={workbenchSurface.label}>
          {t("workbench.workspaceContext", "Workspace context")}
        </p>
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
            count={attachmentsCount}
          >
            <InlineList
              items={attachmentNames}
              empty={t("workbench.contextPanel.noFiles")}
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
