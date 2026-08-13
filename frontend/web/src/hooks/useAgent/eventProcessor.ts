/**
 * Unified message event processor.
 *
 * Single source of truth for transforming message state in response to events.
 * Both streaming (eventHandlers.ts) and history (historyLoader.ts) delegate here.
 *
 * Side effects like subagent stack push/pop, connection status, etc.
 * are handled by the caller based on event type.
 */

import type {
  MessagePart,
  MessageAttachment,
  ToolCall,
  ToolResult,
  TokenUsagePart,
  SandboxPart,
  RunStatusPart,
  ArtifactPart,
  TodoPart,
  SummaryPart,
} from "../../types";
import type { ExecutionTimelinePart } from "../../types/message";
import {
  collapsePublicExecutionSteps,
  upsertPublicExecutionStep,
} from "./publicStreamPresentation";
import i18n from "../../i18n";
import { translateBackendError } from "../../utils/backendErrors";
import {
  CHAT_PUBLIC_PROGRESS_EVENT_TYPES,
  CHAT_PUBLIC_PROJECTION_VERSION,
  isPublicAgentProgressEvent,
  isAssistantTextProjection,
  isPublicExecutionEvent,
  PUBLIC_EXECUTION_EVENT_TYPES,
  type EventData,
  type SubagentStackItem,
} from "./types";
import {
  addPartToDepth,
  createSubagentPart,
  createThinkingPart,
  updateSubagentResult,
  clearAllLoadingStates,
} from "./messageParts";
import type { ThinkingPart } from "../../types";

// ============================================
// Shared utilities
// ============================================

/**
 * Convert backend attachment format to frontend format.
 */
export function convertAttachments(
  attachments?: Array<{
    id: string;
    key: string;
    name: string;
    type: string;
    mime_type: string;
    size: number;
    url: string;
  }>,
): MessageAttachment[] | undefined {
  return attachments?.map((a) => ({
    id: a.id,
    key: a.key,
    name: a.name,
    type: a.type as MessageAttachment["type"],
    mimeType: a.mime_type,
    size: a.size,
    url: a.url,
  }));
}

// ============================================
// Event processor
// ============================================

/**
 * Result of processing a message event.
 */
export interface ProcessMessageEventResult {
  parts: MessagePart[];
  content: string;
  toolCalls: ToolCall[];
  toolResult?: ToolResult;
  tokenUsage?: TokenUsagePart;
  duration?: number;
  cancelled?: boolean;
}

function safeEventError(error: unknown): string | undefined {
  if (typeof error !== "string" || !error) return undefined;
  return translateBackendError(error, i18n.t.bind(i18n));
}

const CHAT_PUBLIC_COMMENTARY_EVENT_TYPES: ReadonlySet<string> = new Set(
  [...CHAT_PUBLIC_PROGRESS_EVENT_TYPES].filter(
    (eventType) =>
      eventType !== "tool_call_started" &&
      eventType !== "tool_call_completed" &&
      eventType !== "tool_denied",
  ),
);
const CHAT_PUBLIC_STATUS_EVENT_TYPES: ReadonlySet<string> = new Set([
  ...CHAT_PUBLIC_COMMENTARY_EVENT_TYPES,
  "error",
]);
const MAX_PUBLIC_ACTIVITY_TIMELINE_PARTS = 12;
const ACTIONABLE_PUBLIC_STATUS_PATTERN =
  /error|failed|failure|denied|blocked|forbidden|unauthori[sz]ed|cancel/i;

interface PublicTerminalPresentation {
  detailKind: "failed" | "cancelled";
  message: string;
  stage: string;
  severity: "info" | "warning" | "error";
}

function publicTerminalPresentation(
  detailCode: string,
): PublicTerminalPresentation | undefined {
  const failed = (
    message: string,
    stage = "terminal",
  ): PublicTerminalPresentation => ({
    detailKind: "failed",
    message,
    stage,
    severity: "error",
  });
  const presentations: Record<string, PublicTerminalPresentation> = {
    run_failed: failed(i18n.t("chat.runTerminal.failed")),
    run_timeout: failed(
      i18n.t("chat.runTerminal.runTimeout", {
        defaultValue: "任务执行超时。请缩小任务范围后重试。",
      }),
    ),
    run_budget_exhausted: failed(
      i18n.t("chat.runTerminal.runBudgetExhausted"),
    ),
    model_service_unavailable: failed(
      i18n.t("chat.runTerminal.modelServiceUnavailable", {
        defaultValue:
          "模型服务暂时不可用。请稍后重试；如问题持续，请联系管理员。",
      }),
    ),
    execution_service_unavailable: failed(
      i18n.t("chat.runTerminal.executionServiceUnavailable", {
        defaultValue:
          "AI 执行服务暂时不可用。请稍后重试；如问题持续，请联系管理员。",
      }),
    ),
    dependent_service_unavailable: failed(
      i18n.t("chat.runTerminal.dependentServiceUnavailable", {
        defaultValue: "任务依赖的服务暂时不可用。请稍后重试。",
      }),
    ),
    capability_not_authorized: failed(
      i18n.t("chat.runTerminal.capabilityNotAuthorized", {
        defaultValue: "当前账号不能使用所选能力。请重新选择或联系管理员。",
      }),
      "policy",
    ),
    tool_not_authorized: failed(
      i18n.t("chat.runTerminal.toolNotAuthorized", {
        defaultValue: "任务所需工具未获授权。请调整请求或联系管理员。",
      }),
      "policy",
    ),
    skill_sandbox_admission_failed: failed(
      i18n.t("chat.runTerminal.skillSandboxAdmissionFailed"),
      "skill_sandbox_admission",
    ),
    context_file_too_large: failed(
      i18n.t("chat.runTerminal.contextFileTooLarge"),
      "file_preprocessing",
    ),
    run_cancelled: {
      detailKind: "cancelled",
      message: i18n.t("chat.runTerminal.cancelledWithPartial", {
        defaultValue: "任务已取消。取消前已产生的公开内容仍会保留。",
      }),
      stage: "terminal",
      severity: "warning",
    },
  };
  return presentations[detailCode];
}

/**
 * Unified message event processor.
 */
export function processMessageEvent(
  eventType: string,
  data: EventData,
  parts: MessagePart[],
  content: string,
  toolCalls: ToolCall[],
  depth: number,
  subagentStack: SubagentStackItem[],
  isStreaming: boolean,
  messageId?: string,
): ProcessMessageEventResult {
  const result: ProcessMessageEventResult = { parts, content, toolCalls };
  const agentId = data.agent_id;
  if (eventType === "tool:start" || eventType === "tool:result") {
    // Legacy tool frames carry an unversioned raw-tool surface. They are not a
    // fallback for public execution v1 and must not create renderable parts.
    return result;
  }

  switch (eventType) {
    // ---- Agent events ----

    case "agent:call": {
      const subagentPart = createSubagentPart(
        agentId || "unknown",
        data.agent_name || agentId || i18n.t("chat.unknownAgent"),
        data.input || "",
        depth,
        data.timestamp,
      );
      result.parts = addPartToDepth(
        parts,
        subagentPart,
        depth,
        subagentStack,
        agentId || "unknown",
        messageId,
      );
      break;
    }

    case "agent:result": {
      result.parts = updateSubagentResult(
        parts,
        agentId || "unknown",
        String(data.result || ""),
        data.success !== false,
        depth,
        safeEventError(data.error),
        data.timestamp,
      );
      break;
    }

    // ---- Thinking events ----

    case "thinking": {
      const thinkingContent = data.content || "";
      if (!thinkingContent) break;

      const thinkingPart = createThinkingPart(
        thinkingContent,
        data.thinking_id,
        depth,
        agentId,
        isStreaming,
      );

      if (depth > 0) {
        result.parts = addPartToDepth(
          parts,
          thinkingPart,
          depth,
          subagentStack,
          agentId,
          messageId,
        );
      } else {
        const newParts = [...parts];
        let existingIndex = -1;

        if (data.thinking_id !== undefined) {
          existingIndex = newParts.findIndex(
            (p) => p.type === "thinking" && p.thinking_id === data.thinking_id,
          );
        } else {
          for (let i = newParts.length - 1; i >= 0; i--) {
            const p = newParts[i];
            if (p.type === "thinking" && p.thinking_id === undefined) {
              existingIndex = i;
              break;
            }
          }
        }

        if (existingIndex >= 0) {
          const existing = newParts[existingIndex] as ThinkingPart;
          newParts[existingIndex] = {
            ...existing,
            content: existing.content + thinkingContent,
            isStreaming: isStreaming ? true : existing.isStreaming,
          };
        } else {
          newParts.push(thinkingPart);
        }
        result.parts = newParts;
      }
      break;
    }

    // ---- Message chunk events ----

    case "message:chunk": {
      const assistantProjection = isAssistantTextProjection(data);
      if (data.projection_version && !assistantProjection) break;
      const chunkContent = data.content || "";
      if (!chunkContent) break;

      if (
        assistantProjection &&
        data.projection_kind === "assistant_final"
      ) {
        if (depth > 0) break;
        result.parts = replaceAssistantTextWithFinal(parts, chunkContent);
        result.content = chunkContent;
        break;
      }

      if (depth > 0) {
        const textPart = {
          type: "text" as const,
          content: chunkContent,
          depth,
          agent_id: agentId,
        };
        result.parts = addPartToDepth(
          parts,
          textPart,
          depth,
          subagentStack,
          agentId,
          messageId,
        );
      } else {
        const newParts = [...parts];
        const lastPart = newParts[newParts.length - 1];
        if (lastPart?.type === "text" && !lastPart.depth) {
          newParts[newParts.length - 1] = {
            ...lastPart,
            content: lastPart.content + chunkContent,
          };
        } else {
          newParts.push({ type: "text" as const, content: chunkContent });
        }
        result.parts = newParts;
        result.content = content + chunkContent;
      }
      break;
    }

    // ---- Controlled terminal detail ----

    case "final_detail": {
      // Terminal detail is a fixed-code presentation contract. Never render
      // the backend-provided message itself: an unknown code or mismatched kind
      // fails closed, and useful partial assistant text remains intact.
      const detailCode = data.detail_code || "";
      const terminal = publicTerminalPresentation(detailCode);
      if (!terminal || data.detail_kind !== terminal.detailKind) break;
      const partialContent =
        content ||
        parts
          .filter((part) => part.type === "text" && !part.depth)
          .map((part) => (part.type === "text" ? part.content : ""))
          .join("");
      result.content = partialContent || terminal.message;
      result.cancelled = terminal.detailKind === "cancelled";
      result.parts = upsertRunStatusPart(parts, {
        type: "run_status",
        event_id: `terminal-detail:${data.run_id || messageId || detailCode}`,
        event_type: detailCode,
        stage: terminal.stage,
        message: terminal.message,
        severity: terminal.severity,
        created_at: data.timestamp,
      });
      break;
    }

    // ---- Tool events ----

    case "execution_step":
    case "execution_progress":
    case "execution_step_completed":
    case "execution_step_failed": {
      const executionPart = createExecutionTimelinePart(eventType, data);
      if (executionPart) {
        result.parts = upsertPublicExecutionStep(parts, executionPart);
      }
      break;
    }

    // ---- Sandbox events ----

    case "sandbox:starting": {
      const sandboxPart: SandboxPart = {
        type: "sandbox",
        status: "starting",
        timestamp: data.timestamp,
      };
      result.parts = upsertSandboxPart(parts, sandboxPart);
      break;
    }

    case "sandbox:ready": {
      const readyPart: SandboxPart = {
        type: "sandbox",
        status: "ready",
        sandbox_id: data.sandbox_id,
        timestamp: data.timestamp,
      };
      result.parts = upsertSandboxPart(parts, readyPart);
      break;
    }

    case "sandbox:error": {
      const errorPart: SandboxPart = {
        type: "sandbox",
        status: "error",
        error: safeEventError(data.error),
        timestamp: data.timestamp,
      };
      result.parts = upsertSandboxPart(parts, errorPart);
      break;
    }

    // ---- Token usage ----

    case "token:usage": {
      result.tokenUsage = {
        type: "token_usage",
        input_tokens: data.input_tokens || 0,
        output_tokens: data.output_tokens || 0,
        total_tokens: data.total_tokens || 0,
        cache_creation_tokens: data.cache_creation_tokens || 0,
        cache_read_tokens: data.cache_read_tokens || 0,
        model_id: data.model_id,
        model: data.model,
      };
      if (data.duration) result.duration = data.duration * 1000;
      break;
    }

    // ---- ai-platform run playback events ----

    case "run_event": {
      const executionKind = String(data.event_type || "");
      if (PUBLIC_EXECUTION_EVENT_TYPES.has(executionKind as never)) {
        const executionPart = createExecutionTimelinePart(executionKind, data);
        if (executionPart) {
          result.parts = upsertPublicExecutionStep(parts, executionPart);
        }
        break;
      }
      if (!shouldProjectRunStatus(data)) {
        break;
      }
      const eventId = String(data.event_id || "");
      if (!eventId) break;
      const severity =
        data.severity === "warning" || data.severity === "error"
          ? data.severity
          : "info";
      const runStatusPart: RunStatusPart = {
        type: "run_status",
        event_id: eventId,
        event_type: String(data.event_type || data.type || "status"),
        stage: String(data.stage || data.status || ""),
        message: String(data.message || data.content || ""),
        severity,
        sequence:
          typeof data.sequence === "number" ? data.sequence : undefined,
        created_at: data.created_at || data.timestamp,
      };
      result.parts = upsertRunStatusPart(parts, runStatusPart);
      break;
    }

    case "artifact_card": {
      const artifactId = String(data.artifact_id || "");
      if (!artifactId) break;
      const artifactPart: ArtifactPart = {
        type: "artifact",
        artifact_id: artifactId,
        artifact_type: String(data.artifact_type || "artifact"),
        label: String(data.label || data.artifact_type || "Artifact"),
        content_type: String(data.content_type || "application/octet-stream"),
        size_bytes:
          typeof data.size_bytes === "number" ? data.size_bytes : 0,
        download_url:
          typeof data.download_url === "string"
            ? data.download_url
            : undefined,
        preview_url:
          typeof data.preview_url === "string" || data.preview_url === null
            ? data.preview_url
            : undefined,
        status: typeof data.status === "string" ? data.status : undefined,
        created_at: data.created_at || data.timestamp,
      };
      result.parts = upsertArtifactPart(parts, artifactPart);
      break;
    }

    // ---- Error ----

    // ---- Todo events ----

    case "todo:updated": {
      const todos = (data.todos || []) as TodoPart["items"];
      if (!todos.length) break;
      const todoPart: TodoPart = { type: "todo", items: todos, isStreaming };
      if (depth > 0) {
        result.parts = addPartToDepth(
          parts,
          todoPart,
          depth,
          subagentStack,
          agentId,
          messageId,
        );
      } else {
        result.parts = upsertTodoPart(parts, todoPart);
      }
      break;
    }

    // ---- Summary events ----

    case "summary": {
      const summaryContent = data.content || "";
      if (!summaryContent) break;

      const summaryPart: SummaryPart = {
        type: "summary",
        content: summaryContent,
        summary_id: data.summary_id,
        depth,
        agent_id: agentId,
        isStreaming,
      };

      if (depth > 0) {
        result.parts = addPartToDepth(
          parts,
          summaryPart,
          depth,
          subagentStack,
          agentId,
          messageId,
        );
      } else {
        const newParts = [...parts];
        let lastSummaryIdx = -1;
        for (let i = newParts.length - 1; i >= 0; i--) {
          const p = newParts[i];
          if (p.type === "summary" && p.summary_id === data.summary_id) {
            lastSummaryIdx = i;
            break;
          }
        }
        if (lastSummaryIdx >= 0) {
          const existing = newParts[lastSummaryIdx] as SummaryPart;
          newParts[lastSummaryIdx] = {
            ...existing,
            content: existing.content + summaryContent,
          };
        } else {
          newParts.push(summaryPart);
        }
        result.parts = newParts;
      }
      break;
    }

    // ---- Error ----

    case "error": {
      const errorMsg = data.error
        ? translateBackendError(data.error, i18n.t.bind(i18n))
        : i18n.t("chat.unknownError");
      const isCancelled = data.type === "CancelledError";
      result.parts = isStreaming ? clearAllLoadingStates(parts) : parts;
      result.cancelled = isCancelled;
      if (!isCancelled) {
        result.content = i18n.t("chat.errorPrefix", { error: errorMsg });
      }
      break;
    }
  }

  if (!isStreaming) {
    result.parts = collapsePublicExecutionSteps(result.parts);
  }
  return result;
}

// ============================================
// Internal helpers
// ============================================

/** Replace existing sandbox part or append if none exists. */
function upsertSandboxPart(
  parts: MessagePart[],
  sandboxPart: SandboxPart,
): MessagePart[] {
  return parts.some((p) => p.type === "sandbox")
    ? parts.map((p) => (p.type === "sandbox" ? sandboxPart : p))
    : [...parts, sandboxPart];
}

/** Replace existing todo part or append if none exists. */
function upsertTodoPart(
  parts: MessagePart[],
  todoPart: TodoPart,
): MessagePart[] {
  return parts.some((p) => p.type === "todo")
    ? parts.map((p) => (p.type === "todo" ? todoPart : p))
    : [...parts, todoPart];
}

/** Only routine informational commentary may be compacted or evicted. */
function isReplaceableInformationalCommentaryPart(
  part: MessagePart,
): part is RunStatusPart {
  return (
    part.type === "run_status" &&
    part.severity === "info" &&
    CHAT_PUBLIC_COMMENTARY_EVENT_TYPES.has(part.event_type) &&
    !ACTIONABLE_PUBLIC_STATUS_PATTERN.test(part.event_type)
  );
}

/** Replace an existing platform run event projection by event id. */
function upsertRunStatusPart(
  parts: MessagePart[],
  runStatusPart: RunStatusPart,
): MessagePart[] {
  if (
    parts.some(
      (part) =>
        part.type === "run_status" &&
        part.event_id === runStatusPart.event_id,
    )
  ) {
    return parts.map((part) =>
      part.type === "run_status" &&
      part.event_id === runStatusPart.event_id
        ? runStatusPart
        : part,
    );
  }
  if (CHAT_PUBLIC_PROGRESS_EVENT_TYPES.has(runStatusPart.event_type)) {
    let lastReplaceableIndex = -1;
    for (let index = parts.length - 1; index >= 0; index -= 1) {
      const part = parts[index];
      if (isReplaceableInformationalCommentaryPart(part)) {
        lastReplaceableIndex = index;
        break;
      }
    }
    const lastReplaceable =
      lastReplaceableIndex >= 0 ? parts[lastReplaceableIndex] : undefined;
    let nextParts = [...parts, runStatusPart];
    if (
      isReplaceableInformationalCommentaryPart(runStatusPart) &&
      lastReplaceable?.type === "run_status" &&
      lastReplaceable.event_type === runStatusPart.event_type &&
      lastReplaceable.stage === runStatusPart.stage &&
      lastReplaceable.message === runStatusPart.message
    ) {
      nextParts = parts.map((part, index) =>
        index === lastReplaceableIndex ? runStatusPart : part,
      );
    }
    const replaceableIndexes = nextParts.flatMap((part, index) =>
      isReplaceableInformationalCommentaryPart(part) ? [index] : [],
    );
    const overflow =
      replaceableIndexes.length - MAX_PUBLIC_ACTIVITY_TIMELINE_PARTS;
    if (overflow <= 0) return nextParts;
    const removedIndexes = new Set(replaceableIndexes.slice(0, overflow));
    return nextParts.filter((_part, index) => !removedIndexes.has(index));
  }
  return [...parts, runStatusPart];
}

function createExecutionTimelinePart(
  eventType: string,
  data: EventData,
): ExecutionTimelinePart | null {
  const publicEvent = normalizePublicExecutionEvent(eventType, data);
  if (!publicEvent) {
    return null;
  }
  return {
    type: "execution_step",
    sequence: publicEvent.sequence,
    step_id: publicEvent.step_id,
    kind: publicEvent.kind,
    presentation_kind: publicEvent.presentation_kind,
    stage: publicEvent.stage,
    title:
      typeof publicEvent.title === "string"
        ? publicEvent.title
        : typeof publicEvent.safe_label === "string"
          ? publicEvent.safe_label
          : undefined,
    summary:
      typeof publicEvent.summary === "string"
        ? publicEvent.summary
        : undefined,
    status: publicEvent.status,
    progress: publicEvent.progress,
    safe_file_name: safePublicExecutionFileName(publicEvent.safe_file_name),
  };
}

function safePublicExecutionFileName(value: string | null): string | null {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.length > 128 ||
    value !== value.trim() ||
    value === "." ||
    value === ".." ||
    /[\\/:*?"<>|]/.test(value) ||
    [...value].some((character) => character.charCodeAt(0) < 32)
  ) {
    return null;
  }
  return value;
}

const PUBLIC_EXECUTION_V1_FIELDS = new Set([
  "schema_version",
  "event_id",
  "sequence",
  "run_id",
  "step_id",
  "kind",
  "stage",
  "status",
  "title",
  "summary",
  "progress",
  "safe_file_name",
  "artifact_public_id",
  "created_at",
]);
const PUBLIC_EXECUTION_V2_FIELDS = new Set([
  "schema_version",
  "event_id",
  "sequence",
  "run_id",
  "step_id",
  "presentation_kind",
  "kind",
  "stage",
  "status",
  "progress",
  "safe_label",
  "created_at",
]);
const PUBLIC_EXECUTION_HISTORY_ENVELOPE_FIELDS = new Set([
  ...PUBLIC_EXECUTION_V1_FIELDS,
  ...PUBLIC_EXECUTION_V2_FIELDS,
  "event_type",
  "timestamp",
]);

type ValidPublicExecutionEvent = EventData & {
  sequence: number;
  step_id: string;
  kind: ExecutionTimelinePart["kind"];
  status: ExecutionTimelinePart["status"];
  progress: ExecutionTimelinePart["progress"];
  safe_file_name: string | null;
  stage: string;
  presentation_kind?: string;
  title?: string;
  summary?: string;
  safe_label?: string;
};

function normalizePublicExecutionEvent(
  eventType: string,
  data: EventData,
): ValidPublicExecutionEvent | null {
  if (isPublicExecutionEvent(eventType, data)) return data;
  const source = data as Record<string, unknown>;
  if (
    source.event_type !== eventType ||
    (source.timestamp !== undefined && typeof source.timestamp !== "string") ||
    !Object.keys(source).every((key) =>
      PUBLIC_EXECUTION_HISTORY_ENVELOPE_FIELDS.has(key),
    )
  ) {
    return null;
  }
  const fields =
    source.schema_version === "ai-platform.public-execution-event.v2"
      ? PUBLIC_EXECUTION_V2_FIELDS
      : PUBLIC_EXECUTION_V1_FIELDS;
  const normalized = Object.fromEntries(
    [...fields].map((key) => [key, source[key]]),
  ) as EventData;
  return isPublicExecutionEvent(eventType, normalized) ? normalized : null;
}

function shouldProjectRunStatus(data: EventData): boolean {
  const payload = asRecord(data.payload);
  if (payload.visible_to_user === false || payload.visibleToUser === false) {
    return false;
  }
  const eventType = String(data.event_type || data.type || "").toLowerCase();
  if (eventType === "agent_public_progress") {
    return isPublicAgentProgressEvent(data);
  }
  return (
    data.projection_version === CHAT_PUBLIC_PROJECTION_VERSION &&
    CHAT_PUBLIC_STATUS_EVENT_TYPES.has(eventType)
  );
}

function replaceAssistantTextWithFinal(
  parts: MessagePart[],
  content: string,
): MessagePart[] {
  let replacedText = false;
  const converged = parts.flatMap((part): MessagePart[] => {
    if (
      part.type === "run_status" &&
      part.severity === "info" &&
      CHAT_PUBLIC_PROGRESS_EVENT_TYPES.has(part.event_type)
    ) {
      return [];
    }
    if (part.type !== "text" || part.depth) {
      return [part];
    }
    if (replacedText) {
      return [];
    }
    replacedText = true;
    return [{ ...part, content }];
  });
  return replacedText ? converged : [...converged, { type: "text", content }];
}

/** Replace an existing platform artifact card by artifact id. */
function upsertArtifactPart(
  parts: MessagePart[],
  artifactPart: ArtifactPart,
): MessagePart[] {
  return parts.some(
    (p) => p.type === "artifact" && p.artifact_id === artifactPart.artifact_id,
  )
    ? parts.map((p) =>
        p.type === "artifact" && p.artifact_id === artifactPart.artifact_id
          ? artifactPart
          : p,
      )
    : [...parts, artifactPart];
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}
