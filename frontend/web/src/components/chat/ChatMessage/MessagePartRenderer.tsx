import { clsx } from "clsx";
import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Ban,
  CheckCircle,
  Clock3,
  Download,
  Eye,
  LoaderCircle,
  ShieldAlert,
  XCircle,
} from "lucide-react";
import type { MessagePart } from "../../../types";
import {
  getPublicTerminalPresentationDefinition,
  publicTerminalRunReference,
} from "../../../hooks/useAgent/publicTerminalPresentation";
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
import { PublicExecutionProcess } from "./PublicExecutionProcess";
import type { RevealPreviewRequest } from "./items/revealPreviewData";
import type { RevealPreviewOpenSource } from "./items/revealPreviewState";
import { createToolPartAnchorId } from "./messagePartAnchors";
import {
  getOrdinaryUserToolPermissionPresentation,
} from "./toolPermissionCardState";
import { buildArtifactPreviewRequest } from "./items/artifactPreview";
import { downloadArtifactFile } from "./items/artifactDownload";
import {
  getArtifactDownloadController,
  type ArtifactDownloadController,
  type ArtifactDownloadScope,
  type ArtifactDownloadState,
} from "./items/artifactDownloadRegistry";

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
  artifactDownloadScope,
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
  artifactDownloadScope?: ArtifactDownloadScope;
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
        status={part.status}
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
        parent_agent_id={part.parent_agent_id}
        duration_ms={part.duration_ms}
        progress_percent={part.progress_percent}
        current_category={part.current_category}
        artifactDownloadScope={artifactDownloadScope}
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
    return <RunStatusItem part={part} isStreaming={isStreaming === true} />;
  }

  if (part.type === "tool_permission") {
    return <ToolPermissionCardItem part={part} />;
  }

  if (part.type === "artifact") {
    return (
      <ArtifactCardItem
        part={part}
        onOpenPreview={onOpenPreview}
        artifactDownloadScope={artifactDownloadScope}
      />
    );
  }

  if (part.type === "execution_step") {
    return (
      <PublicExecutionProcess
        steps={[part]}
        isStreaming={isStreaming === true}
        expandable={false}
      />
    );
  }

  if (part.type === "execution_process") {
    return <PublicExecutionProcess steps={part.steps} isStreaming={false} />;
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

const RUN_STATUS_EVENT_I18N_KEYS: Readonly<Record<string, string>> = {
  queued: "chat.runStatus.event.queued",
  run_started: "chat.runStatus.event.runStarted",
  tool_call_started: "chat.runStatus.event.toolCallStarted",
  tool_call_completed: "chat.runStatus.event.toolCallCompleted",
  agent_step_started: "chat.runStatus.event.agentStepStarted",
  agent_step_reused: "chat.runStatus.event.agentStepReused",
  agent_step_completed: "chat.runStatus.event.agentStepCompleted",
  agent_step_blocked: "chat.runStatus.event.agentStepBlocked",
  agent_step_failed: "chat.runStatus.event.agentStepFailed",
  subagent_started: "chat.runStatus.event.subagentStarted",
  subagent_completed: "chat.runStatus.event.subagentCompleted",
  subagent_failed: "chat.runStatus.event.subagentFailed",
  run_child_created: "chat.runStatus.event.runChildCreated",
  capability_selected: "chat.runStatus.event.capabilitySelected",
  intent_detected: "chat.runStatus.event.intentDetected",
  intent_confirmed: "chat.runStatus.event.intentConfirmed",
  context_snapshot_created: "chat.runStatus.event.contextSnapshotCreated",
  file_bound: "chat.runStatus.event.fileBound",
  artifact_created: "chat.runStatus.event.artifactCreated",
  cancel_requested: "chat.runStatus.event.cancelRequested",
  cancel_requested_but_completed:
    "chat.runStatus.event.cancelRequestedButCompleted",
  status_unavailable: "chat.runStatus.event.statusUnavailable",
  terminal_result_unavailable:
    "chat.runStatus.event.terminalResultUnavailable",
};

const RUN_STATUS_DETAIL_I18N_KEYS: Readonly<Record<string, string>> = {
  status_unavailable: "chat.runTerminal.statusUnavailable",
  terminal_result_unavailable: "chat.runTerminal.terminalResultUnavailable",
};

function RunStatusItem({
  part,
  isStreaming,
}: {
  part: Extract<MessagePart, { type: "run_status" }>;
  isStreaming: boolean;
}) {
  const { t } = useTranslation();
  const isWaiting =
    part.event_type === "queued" ||
    part.event_type === "agent_step_blocked" ||
    part.event_type === "cancel_requested";
  const isActive =
    part.event_type === "run_started" ||
    part.event_type === "tool_call_started" ||
    part.event_type === "agent_step_started" ||
    part.event_type === "agent_step_reused" ||
    part.event_type === "subagent_started" ||
    part.event_type === "run_child_created";
  const Icon =
    part.severity === "error"
      ? XCircle
      : part.severity === "warning"
        ? AlertTriangle
        : isWaiting
          ? Clock3
          : isActive
            ? LoaderCircle
            : CheckCircle;
  const tone =
    part.severity === "error"
      ? "border-red-200/70 bg-red-50 text-red-700 dark:border-red-900/50 dark:bg-red-950/20 dark:text-red-300"
      : part.severity === "warning"
        ? "border-amber-200/70 bg-amber-50 text-amber-700 dark:border-amber-900/50 dark:bg-amber-950/20 dark:text-amber-300"
        : "border-stone-200/70 bg-stone-50 text-stone-700 dark:border-stone-700/60 dark:bg-stone-800/40 dark:text-stone-300";
  const terminalDefinition = getPublicTerminalPresentationDefinition(
    part.event_type,
  );
  const eventLabel = terminalDefinition
    ? t(terminalDefinition.eventLabelKey, {
        defaultValue: terminalDefinition.defaultEventLabel,
      })
    : t(
        RUN_STATUS_EVENT_I18N_KEYS[part.event_type] ??
          "chat.runStatus.event.executionUpdate",
      );
  const detailKey = RUN_STATUS_DETAIL_I18N_KEYS[part.event_type];
  const statusLabel = terminalDefinition
    ? t(terminalDefinition.messageKey, {
        defaultValue: terminalDefinition.defaultMessage,
      })
    : t(
        detailKey ??
          (part.severity === "error"
            ? "chat.runStatus.status.failed"
            : part.severity === "warning"
              ? "chat.runStatus.status.warning"
              : isWaiting
                ? "chat.runStatus.status.waiting"
                : isActive
                  ? "chat.runStatus.status.running"
                  : "chat.runStatus.status.completed"),
      );
  const runReference =
    part.event_type === "terminal_reconciliation_failed"
      ? publicTerminalRunReference(part.run_reference)
      : undefined;
  const runReferenceLabel = runReference
    ? t("chat.runTerminal.runReference", {
        defaultValue: "任务编号：{{runId}}",
        runId: runReference,
      })
    : null;

  return (
    <div
      role={part.severity === "info" ? "status" : "alert"}
      className={clsx(
        "my-1 flex min-w-0 items-start gap-2 rounded-lg border px-3 py-2 text-sm",
        tone,
      )}
    >
      <Icon
        size={15}
        className={clsx(
          "mt-0.5 shrink-0",
          isStreaming && isActive && "animate-spin",
        )}
      />
      <div className="min-w-0 flex-1">
        <div className="break-words font-medium leading-snug">{eventLabel}</div>
        <div
          className={clsx(
            "mt-0.5 text-xs opacity-70",
            terminalDefinition || detailKey ? "break-words" : "truncate",
          )}
        >
          {statusLabel}
          {runReferenceLabel ? ` ${runReferenceLabel}` : ""}
        </div>
      </div>
    </div>
  );
}

const ARTIFACT_DOWNLOAD_FAILURE_MESSAGE = "下载失败，请稍后重试。";
const ARTIFACT_DOWNLOAD_RETRY_LABEL = "重试下载";
const messagePartObjectTokens = new WeakMap<object, number>();
let nextMessagePartObjectToken = 1;

function getMessagePartObjectToken(part: MessagePart): number {
  const existing = messagePartObjectTokens.get(part);
  if (existing !== undefined) {
    return existing;
  }
  const token = nextMessagePartObjectToken;
  nextMessagePartObjectToken += 1;
  messagePartObjectTokens.set(part, token);
  return token;
}

function createMessagePartIdentity(part: MessagePart, index: number): string {
  switch (part.type) {
    case "text":
      return `${part.type}:${part.logical_id || `index-${index}`}`;
    case "artifact":
      return `${part.type}:${part.artifact_id}`;
    case "tool":
      return part.id
        ? `${part.type}:${part.id}`
        : `${part.type}:object:${getMessagePartObjectToken(part)}`;
    case "run_status":
    case "tool_permission":
      return part.event_id
        ? `${part.type}:${part.event_id}`
        : `${part.type}:object:${getMessagePartObjectToken(part)}`;
    case "execution_step":
      return `${part.type}:${part.step_id}`;
    case "execution_process":
      return part.type;
    case "thinking":
      return part.thinking_id
        ? `${part.type}:${part.thinking_id}`
        : `${part.type}:object:${getMessagePartObjectToken(part)}`;
    case "summary":
      return part.summary_id
        ? `${part.type}:${part.summary_id}`
        : `${part.type}:object:${getMessagePartObjectToken(part)}`;
    case "subagent":
      return `${part.type}:${part.agent_id}:${part.startedAt ?? "pending"}`;
    default:
      return `${part.type}:object:${getMessagePartObjectToken(part)}`;
  }
}

// eslint-disable-next-line react-refresh/only-export-components -- independently tested reconciliation seam.
export function createMessagePartRenderKeys(
  messageId: string,
  parts: MessagePart[],
): string[] {
  return parts.map(
    (part, index) => `${messageId}:${createMessagePartIdentity(part, index)}`,
  );
}

function ArtifactCardItem({
  part,
  onOpenPreview,
  artifactDownloadScope,
}: {
  part: Extract<MessagePart, { type: "artifact" }>;
  onOpenPreview?: (
    preview: RevealPreviewRequest,
    source?: RevealPreviewOpenSource,
  ) => boolean;
  artifactDownloadScope?: ArtifactDownloadScope;
}) {
  const { t } = useTranslation();
  const localDownloadControllerRef = useRef<ArtifactDownloadController | null>(null);
  const scopedDownloadController = getArtifactDownloadController(
    artifactDownloadScope,
    part.artifact_id,
  );
  if (!localDownloadControllerRef.current) {
    let isDownloadInFlight = false;
    let state: ArtifactDownloadState = "idle";
    const listeners = new Set<(next: ArtifactDownloadState) => void>();
    const setState = (next: ArtifactDownloadState) => {
      state = next;
      listeners.forEach((listener) => listener(next));
    };
    localDownloadControllerRef.current = {
      getState: () => state,
      subscribe(listener) {
        listeners.add(listener);
        listener(state);
        return () => listeners.delete(listener);
      },
      async download(downloadArtifact) {
        if (isDownloadInFlight) return;
        isDownloadInFlight = true;
        setState("downloading");
        try {
          setState((await downloadArtifact()) ? "idle" : "failed");
        } catch {
          setState("failed");
        } finally {
          isDownloadInFlight = false;
        }
      },
    };
  }
  const activeDownloadController =
    scopedDownloadController ?? localDownloadControllerRef.current;
  const [downloadState, setDownloadState] = useState<ArtifactDownloadState>(
    activeDownloadController.getState(),
  );
  useEffect(
    () => activeDownloadController.subscribe(setDownloadState),
    [activeDownloadController],
  );
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
  const handleDownload = async () => {
    await activeDownloadController.download(() =>
      downloadArtifactFile(part),
    );
  };
  const isDownloading = downloadState === "downloading";
  const hasDownloadFailed = downloadState === "failed";
  const downloadLabel = t(
    hasDownloadFailed ? "common.retry" : "chat.message.download",
    { defaultValue: ARTIFACT_DOWNLOAD_RETRY_LABEL },
  );
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
      role="group"
      aria-label={part.label}
      className={clsx(
        "my-1 flex min-w-0 max-w-xl items-center gap-3 rounded-lg border px-3 py-2.5",
        "flex-wrap",
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
              onClick={() => void handleDownload()}
              disabled={isDownloading}
              aria-label={`${downloadLabel} ${part.label}`}
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-[var(--theme-border)] px-2 text-xs font-medium text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-bg-sidebar)] hover:text-[var(--theme-text)] disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isDownloading ? (
                <LoaderCircle size={13} className="animate-spin" />
              ) : (
                <Download size={13} />
              )}
              <span>{downloadLabel}</span>
            </button>
          )}
        </div>
      )}
      {hasDownloadFailed && (
        <div
          role="alert"
          className="basis-full text-xs text-red-600 dark:text-red-400"
        >
          {ARTIFACT_DOWNLOAD_FAILURE_MESSAGE}
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
