import { clsx } from "clsx";
import {
  AlertTriangle,
  Ban,
  CheckCircle,
  Download,
  Eye,
  ShieldAlert,
  XCircle,
} from "lucide-react";
import type { MessagePart } from "../../../types";
import { useTranslation } from "react-i18next";
import { MarkdownContent } from "./MarkdownContent";
import { formatFileSize, getFileTypeInfo } from "../../documents/utils";
import {
  ToolCallItem,
  FileRevealItem,
  ProjectRevealItem,
  ReadFileItem,
  EditFileItem,
  WriteFileItem,
  GrepItem,
  LsItem,
  GlobItem,
  ExecuteItem,
} from "./ToolCallItem";
import { ThinkingBlock, SubagentBlock, SandboxItem } from "./SubagentBlocks";
import { TodoBlock } from "./TodoBlock";
import { SummaryItem } from "./SummaryItem";
import type { RevealPreviewRequest } from "./items/revealPreviewData";
import type { RevealPreviewOpenSource } from "./items/revealPreviewState";
import { createToolPartAnchorId } from "./messagePartAnchors";
import {
  getOrdinaryUserToolPermissionPresentation,
} from "./toolPermissionCardState";
import { buildArtifactPreviewRequest } from "./items/artifactPreview";
import { downloadArtifactFile } from "./items/artifactDownload";

// Render single message part (shared by main agent and subagent)
export function MessagePartRenderer({
  part,
  messageId,
  partIndex,
  isStreaming,
  isLast,
  allowAutoPreview,
  activePreview,
  onOpenPreview,
}: {
  part: MessagePart;
  messageId?: string;
  partIndex?: number;
  isStreaming?: boolean;
  isLast: boolean;
  allowAutoPreview?: boolean;
  activePreview?: RevealPreviewRequest | null;
  onOpenPreview?: (
    preview: RevealPreviewRequest,
    source?: RevealPreviewOpenSource,
  ) => boolean;
}) {
  const { t } = useTranslation();
  const toolPartAnchorId =
    messageId !== undefined && partIndex !== undefined
      ? createToolPartAnchorId(messageId, partIndex)
      : undefined;

  if (part.type === "text") {
    return (
      <MarkdownContent
        content={part.content}
        isStreaming={isStreaming && isLast}
        headingAnchorContext={
          messageId !== undefined && partIndex !== undefined
            ? {
                messageId,
                partIndex,
              }
            : undefined
        }
      />
    );
  }

  if (part.type === "tool") {
    // Detect Read tool, use dedicated component (strips line numbers, shows file path)
    if (part.name === "read_file") {
      return (
        <ReadFileItem
          args={part.args}
          result={part.result}
          success={part.success}
          isPending={part.isPending}
          cancelled={part.cancelled}
        />
      );
    }
    // Detect reveal_file tool, use dedicated component
    if (part.name === "reveal_file") {
      return (
        <div
          id={toolPartAnchorId}
          className="scroll-mt-6 rounded-lg transition-[box-shadow] duration-300 data-[external-navigation-highlighted=true]:ring-2 data-[external-navigation-highlighted=true]:ring-amber-500/80 data-[external-navigation-highlighted=true]:shadow-[0_0_20px_rgba(245,158,11,0.25)] dark:data-[external-navigation-highlighted=true]:ring-amber-400/60 dark:data-[external-navigation-highlighted=true]:shadow-[0_0_20px_rgba(251,191,36,0.12)]"
        >
          <FileRevealItem
            args={part.args}
            result={part.result}
            success={part.success}
            isPending={part.isPending}
            cancelled={part.cancelled}
            allowAutoPreview={allowAutoPreview}
            activePreview={activePreview}
            onOpenPreview={onOpenPreview}
          />
        </div>
      );
    }
    // Detect reveal_project tool, use dedicated component
    if (part.name === "reveal_project") {
      return (
        <div
          id={toolPartAnchorId}
          className="scroll-mt-6 rounded-lg transition-[box-shadow] duration-300 data-[external-navigation-highlighted=true]:ring-2 data-[external-navigation-highlighted=true]:ring-amber-500/80 data-[external-navigation-highlighted=true]:shadow-[0_0_20px_rgba(245,158,11,0.25)] dark:data-[external-navigation-highlighted=true]:ring-amber-400/60 dark:data-[external-navigation-highlighted=true]:shadow-[0_0_20px_rgba(251,191,36,0.12)]"
        >
          <ProjectRevealItem
            args={part.args}
            result={part.result}
            success={part.success}
            isPending={part.isPending}
            cancelled={part.cancelled}
            allowAutoPreview={allowAutoPreview}
            activePreview={activePreview}
            onOpenPreview={onOpenPreview}
          />
        </div>
      );
    }
    // Detect edit_file tool, use dedicated component
    if (part.name === "edit_file") {
      return (
        <EditFileItem
          args={part.args}
          result={part.result}
          success={part.success}
          isPending={part.isPending}
          cancelled={part.cancelled}
        />
      );
    }
    // Detect write_file tool, use dedicated component
    if (part.name === "write_file") {
      return (
        <WriteFileItem
          args={part.args}
          result={part.result}
          success={part.success}
          isPending={part.isPending}
          cancelled={part.cancelled}
        />
      );
    }
    // Detect grep tool, use dedicated component
    if (part.name === "grep") {
      return (
        <GrepItem
          args={part.args}
          result={part.result}
          success={part.success}
          isPending={part.isPending}
          cancelled={part.cancelled}
        />
      );
    }
    // Detect ls tool, use dedicated component
    if (part.name === "ls") {
      return (
        <LsItem
          args={part.args}
          result={part.result}
          success={part.success}
          isPending={part.isPending}
          cancelled={part.cancelled}
        />
      );
    }
    // Detect glob tool, use dedicated component
    if (part.name === "glob") {
      return (
        <GlobItem
          args={part.args}
          result={part.result}
          success={part.success}
          isPending={part.isPending}
          cancelled={part.cancelled}
        />
      );
    }
    // Detect execute tool, use dedicated component
    if (part.name === "execute") {
      return (
        <ExecuteItem
          args={part.args}
          result={part.result}
          success={part.success}
          isPending={part.isPending}
          cancelled={part.cancelled}
        />
      );
    }
    return (
      <ToolCallItem
        name={part.name}
        args={part.args}
        result={part.result}
        success={part.success}
        isPending={part.isPending}
        cancelled={part.cancelled}
      />
    );
  }

  if (part.type === "thinking") {
    return (
      <ThinkingBlock
        content={part.content}
        isStreaming={isStreaming && isLast && part.isStreaming}
        panelKey={part.thinking_id}
      />
    );
  }

  if (part.type === "subagent") {
    return (
      <SubagentBlock
        agent_id={part.agent_id}
        agent_name={part.agent_name}
        input={part.input}
        result={part.result}
        success={part.success}
        isPending={part.isPending}
        parts={part.parts}
        startedAt={part.startedAt}
        completedAt={part.completedAt}
        status={part.status}
        error={part.error}
      />
    );
  }

  // Sandbox status block
  if (part.type === "sandbox") {
    return (
      <SandboxItem
        status={part.status}
        sandboxId={part.sandbox_id}
        error={part.error}
      />
    );
  }

  // Todo task list block
  if (part.type === "todo") {
    return (
      <TodoBlock
        items={part.items}
        isStreaming={isStreaming && isLast && part.isStreaming}
      />
    );
  }

  // Summary block
  if (part.type === "summary") {
    const panelKey = `summary:${part.agent_id || "root"}:${part.depth || 0}:${
      part.summary_id || "default"
    }`;
    return (
      <SummaryItem
        content={part.content}
        isStreaming={isStreaming && isLast && part.isStreaming}
        panelKey={panelKey}
      />
    );
  }

  if (part.type === "run_status") {
    return <RunStatusItem part={part} />;
  }

  if (part.type === "tool_permission") {
    return <ToolPermissionCardItem part={part} />;
  }

  if (part.type === "artifact") {
    return <ArtifactCardItem part={part} onOpenPreview={onOpenPreview} />;
  }

  if (part.type === "cancelled") {
    return (
      <div
        className={clsx(
          "flex items-center gap-2 px-4 py-2.5 rounded-lg",
          "bg-amber-50 dark:bg-amber-950/40",
          "border border-amber-200/60 dark:border-amber-800/60",
          "text-amber-700 dark:text-amber-400",
          "text-sm font-medium",
        )}
      >
        <Ban size={16} className="shrink-0" />
        <span>{t("chat.message.cancelled")}</span>
      </div>
    );
  }

  return null;
}

function formatEventLabel(value: string): string {
  return value
    .split(/[_:]+/)
    .filter(Boolean)
    .map((segment) => segment.charAt(0).toUpperCase() + segment.slice(1))
    .join(" ");
}

function RunStatusItem({
  part,
}: {
  part: Extract<MessagePart, { type: "run_status" }>;
}) {
  const Icon =
    part.severity === "error"
      ? XCircle
      : part.severity === "warning"
        ? AlertTriangle
        : CheckCircle;
  const tone =
    part.severity === "error"
      ? "border-red-200/70 bg-red-50 text-red-700 dark:border-red-900/50 dark:bg-red-950/20 dark:text-red-300"
      : part.severity === "warning"
        ? "border-amber-200/70 bg-amber-50 text-amber-700 dark:border-amber-900/50 dark:bg-amber-950/20 dark:text-amber-300"
        : "border-stone-200/70 bg-stone-50 text-stone-700 dark:border-stone-700/60 dark:bg-stone-800/40 dark:text-stone-300";
  const meta = [part.stage, formatEventLabel(part.event_type)]
    .filter(Boolean)
    .join(" · ");

  return (
    <div
      className={clsx(
        "my-1 flex min-w-0 items-start gap-2 rounded-lg border px-3 py-2 text-sm",
        tone,
      )}
    >
      <Icon size={15} className="mt-0.5 shrink-0" />
      <div className="min-w-0 flex-1">
        {part.message && (
          <div className="break-words font-medium leading-snug">
            {part.message}
          </div>
        )}
        {meta && (
          <div className="mt-0.5 truncate text-xs opacity-70">{meta}</div>
        )}
      </div>
    </div>
  );
}

function ArtifactCardItem({
  part,
  onOpenPreview,
}: {
  part: Extract<MessagePart, { type: "artifact" }>;
  onOpenPreview?: (
    preview: RevealPreviewRequest,
    source?: RevealPreviewOpenSource,
  ) => boolean;
}) {
  const { t } = useTranslation();
  const info = getFileTypeInfo(part.label, part.content_type);
  const previewLabel = t("chat.message.preview", { defaultValue: "Preview" });
  const FileIcon = info.icon;
  const sizeText =
    part.size_bytes > 0 ? formatFileSize(part.size_bytes) : info.label;
  const previewRequest = buildArtifactPreviewRequest(part);
  const handlePreview = () => {
    if (!previewRequest || !onOpenPreview) {
      return;
    }
    onOpenPreview(previewRequest, "manual");
  };
  const handleDownload = () => {
    if (!part.download_url) {
      return;
    }
    void downloadArtifactFile(part).catch((error) => {
      console.warn("[ArtifactCardItem] Download failed:", error);
    });
  };
  const body = (
    <>
      <div
        className={clsx(
          "flex h-10 w-10 shrink-0 items-center justify-center rounded-lg",
          info.bg,
        )}
      >
        <FileIcon size={18} className={info.color} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium text-[var(--theme-text)]">
          {part.label}
        </div>
        <div className="mt-0.5 flex min-w-0 items-center gap-2 text-xs text-[var(--theme-text-secondary)]">
          <span className="truncate">{info.label}</span>
          <span className="shrink-0">{sizeText}</span>
        </div>
      </div>
    </>
  );

  return (
    <div
      className={clsx(
        "my-1 flex min-w-0 max-w-xl items-center gap-3 rounded-lg border px-3 py-2.5",
        "border-[var(--theme-border)] bg-[var(--theme-bg-card)] text-left shadow-[0_4px_12px_rgba(18,38,63,0.03)]",
        "dark:bg-stone-900",
      )}
    >
      {body}
      {(previewRequest || part.download_url) && (
        <div className="ml-auto flex shrink-0 items-center gap-1.5">
          {previewRequest && onOpenPreview && (
            <button
              type="button"
              onClick={handlePreview}
              aria-label={`${previewLabel} ${part.label}`}
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-[var(--theme-border)] px-2 text-xs font-medium text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-bg-sidebar)] hover:text-[var(--theme-text)]"
            >
              <Eye size={13} />
              <span>{previewLabel}</span>
            </button>
          )}
          {part.download_url && (
            <button
              type="button"
              onClick={handleDownload}
              aria-label={`${t("chat.message.download")} ${part.label}`}
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-[var(--theme-border)] px-2 text-xs font-medium text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-bg-sidebar)] hover:text-[var(--theme-text)]"
            >
              <Download size={13} />
              <span>{t("chat.message.download")}</span>
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/** Render recorded permission history only; no model-tool action is available. */
export function ToolPermissionCardItem({
  part,
}: {
  part: Extract<MessagePart, { type: "tool_permission" }>;
}) {
  const { t } = useTranslation();
  const presentation = getOrdinaryUserToolPermissionPresentation(part);

  return (
    <div
      className={clsx(
        "my-1 max-w-xl rounded-lg border px-3 py-3 shadow-[0_4px_12px_rgba(18,38,63,0.03)]",
        "border-amber-200/80 bg-amber-50/80 text-stone-800",
        "dark:border-amber-900/60 dark:bg-amber-950/20 dark:text-stone-100",
      )}
    >
      <div className="flex min-w-0 items-start gap-2">
        <ShieldAlert
          size={18}
          className="mt-0.5 shrink-0 text-amber-600 dark:text-amber-400"
        />
        <div className="min-w-0 flex-1">
          <div className="break-words text-sm font-semibold">
            {t(presentation.titleKey)}
          </div>
          <div className="mt-1 text-xs text-stone-600 dark:text-stone-300">
            {t(presentation.messageKey)}
          </div>
        </div>
      </div>
    </div>
  );
}
