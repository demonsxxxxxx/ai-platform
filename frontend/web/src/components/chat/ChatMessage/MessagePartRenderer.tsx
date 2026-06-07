import { clsx } from "clsx";
import {
  AlertTriangle,
  Ban,
  CheckCircle,
  Download,
  Eye,
  ShieldAlert,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { useEffect, useState } from "react";
import type { MessagePart } from "../../../types";
import { useTranslation } from "react-i18next";
import {
  decideToolPermission,
  type ToolPermissionDecision,
} from "../../../services/api/toolPermission";
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
import { syncToolPermissionCardState } from "./toolPermissionCardState";
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
          className="scroll-mt-6 rounded-xl transition-[box-shadow] duration-300 data-[external-navigation-highlighted=true]:ring-2 data-[external-navigation-highlighted=true]:ring-amber-500/80 data-[external-navigation-highlighted=true]:shadow-[0_0_20px_rgba(245,158,11,0.25)] dark:data-[external-navigation-highlighted=true]:ring-amber-400/60 dark:data-[external-navigation-highlighted=true]:shadow-[0_0_20px_rgba(251,191,36,0.12)]"
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
          className="scroll-mt-6 rounded-2xl transition-[box-shadow] duration-300 data-[external-navigation-highlighted=true]:ring-2 data-[external-navigation-highlighted=true]:ring-amber-500/80 data-[external-navigation-highlighted=true]:shadow-[0_0_20px_rgba(245,158,11,0.25)] dark:data-[external-navigation-highlighted=true]:ring-amber-400/60 dark:data-[external-navigation-highlighted=true]:shadow-[0_0_20px_rgba(251,191,36,0.12)]"
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
          "flex items-center gap-2 px-4 py-2.5 rounded-xl",
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
        <div className="truncate text-sm font-medium text-stone-800 dark:text-stone-100">
          {part.label}
        </div>
        <div className="mt-0.5 flex min-w-0 items-center gap-2 text-xs text-stone-500 dark:text-stone-400">
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
        "border-stone-200/70 bg-white text-left shadow-sm",
        "dark:border-stone-700/60 dark:bg-stone-800/50",
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
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-stone-200 px-2 text-xs font-medium text-stone-600 transition-colors hover:bg-stone-50 dark:border-stone-700 dark:text-stone-300 dark:hover:bg-stone-800"
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
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-stone-200 px-2 text-xs font-medium text-stone-600 transition-colors hover:bg-stone-50 dark:border-stone-700 dark:text-stone-300 dark:hover:bg-stone-800"
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

function ToolPermissionCardItem({
  part,
}: {
  part: Extract<MessagePart, { type: "tool_permission" }>;
}) {
  const [status, setStatus] = useState(part.status);
  const [decision, setDecision] = useState(part.decision);
  const [submitting, setSubmitting] =
    useState<ToolPermissionDecision | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const nextState = syncToolPermissionCardState(part, error);
    setStatus(nextState.status);
    setDecision(nextState.decision);
    setError(nextState.error);
  }, [part, error]);

  const isDecided = status === "decided" || Boolean(decision);
  const riskLabel = formatEventLabel(part.risk_level || "low");
  const accessLabel = part.write_capable ? "Write capable" : "Read only";

  const submitDecision = async (nextDecision: ToolPermissionDecision) => {
    setSubmitting(nextDecision);
    setError(null);
    try {
      const response = await decideToolPermission(
        part.run_id,
        part.permission_request_id,
        nextDecision,
      );
      setStatus("decided");
      setDecision(response.permission_request.decision || nextDecision);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Decision failed");
    } finally {
      setSubmitting(null);
    }
  };

  return (
    <div
      className={clsx(
        "my-1 max-w-xl rounded-lg border px-3 py-3 shadow-sm",
        "border-amber-200/80 bg-amber-50/80 text-stone-800",
        "dark:border-amber-900/60 dark:bg-amber-950/20 dark:text-stone-100",
      )}
    >
      <div className="flex min-w-0 items-start gap-2">
        {isDecided ? (
          <ShieldCheck
            size={18}
            className="mt-0.5 shrink-0 text-emerald-600 dark:text-emerald-400"
          />
        ) : (
          <ShieldAlert
            size={18}
            className="mt-0.5 shrink-0 text-amber-600 dark:text-amber-400"
          />
        )}
        <div className="min-w-0 flex-1">
          <div className="break-words text-sm font-semibold">
            Tool permission required
          </div>
          <div className="mt-1 break-all text-xs text-stone-600 dark:text-stone-300">
            {part.tool_id}
          </div>
          <div className="mt-1 flex flex-wrap gap-2 text-xs text-stone-500 dark:text-stone-400">
            <span>{riskLabel} risk</span>
            <span>{accessLabel}</span>
          </div>
        </div>
      </div>

      {isDecided ? (
        <div className="mt-3 rounded-md border border-stone-200/70 bg-white/70 px-2.5 py-1.5 text-xs font-medium text-stone-700 dark:border-stone-700/60 dark:bg-stone-900/30 dark:text-stone-200">
          Decision: {formatPermissionDecision(decision)}
        </div>
      ) : (
        <div className="mt-3 flex flex-wrap gap-2">
          <PermissionDecisionButton
            label="Allow once"
            decision="allow_once"
            submitting={submitting}
            onClick={submitDecision}
          />
          <PermissionDecisionButton
            label="Allow run"
            decision="allow_for_run"
            submitting={submitting}
            onClick={submitDecision}
          />
          <PermissionDecisionButton
            label="Deny"
            decision="deny"
            submitting={submitting}
            onClick={submitDecision}
          />
        </div>
      )}

      {error && (
        <div className="mt-2 break-words text-xs font-medium text-red-600 dark:text-red-300">
          {error}
        </div>
      )}
    </div>
  );
}

function PermissionDecisionButton({
  label,
  decision,
  submitting,
  onClick,
}: {
  label: string;
  decision: ToolPermissionDecision;
  submitting: ToolPermissionDecision | null;
  onClick: (decision: ToolPermissionDecision) => void;
}) {
  const isDeny = decision === "deny";
  const disabled = Boolean(submitting);
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => onClick(decision)}
      className={clsx(
        "inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-60",
        isDeny
          ? "border-red-200 bg-white text-red-700 hover:bg-red-50 dark:border-red-900/60 dark:bg-stone-900/50 dark:text-red-300 dark:hover:bg-red-950/30"
          : "border-stone-200 bg-white text-stone-700 hover:bg-stone-50 dark:border-stone-700 dark:bg-stone-900/50 dark:text-stone-200 dark:hover:bg-stone-800",
      )}
    >
      {isDeny ? <XCircle size={14} /> : <CheckCircle size={14} />}
      <span>{submitting === decision ? "Submitting" : label}</span>
    </button>
  );
}

function formatPermissionDecision(
  decision?: ToolPermissionDecision,
): string {
  if (decision === "allow_once") return "Allow once";
  if (decision === "allow_for_run") return "Allow for run";
  if (decision === "deny") return "Deny";
  return "Pending";
}
