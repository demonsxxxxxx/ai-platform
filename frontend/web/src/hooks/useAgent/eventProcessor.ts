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
  Message,
  MessagePart,
  MessageAttachment,
  ToolCall,
  ToolResult,
  TokenUsagePart,
  SandboxPart,
  RunStatusPart,
  ToolPermissionDecision,
  ToolPermissionPart,
  ToolPermissionStatus,
  ArtifactPart,
  TodoPart,
  SummaryPart,
} from "../../types";
import type { ExecutionTimelinePart } from "../../types/message";
import {
  collapsePublicExecutionSteps,
  projectPublicAgentProgress,
  projectPublicThinkingActivity,
  upsertPublicExecutionStep,
  upsertPublicThinkingActivity,
} from "./publicStreamPresentation";
import {
  publicTerminalPresentation,
  publicTerminalRunReference,
} from "./publicTerminalPresentation";
import i18n from "../../i18n";
import { translateBackendError } from "../../utils/backendErrors";
import {
  CHAT_PUBLIC_PROGRESS_EVENT_TYPES,
  CHAT_PUBLIC_PROJECTION_VERSION,
  PUBLIC_EXECUTION_EVENT_V2_SCHEMA_VERSION,
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

type ToolPermissionPartWithMergeHints = ToolPermissionPart & {
  risk_level_from_event?: boolean;
  write_capable_from_event?: boolean;
};

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
  "public_activity",
  "error",
]);
const MAX_PUBLIC_ACTIVITY_TIMELINE_PARTS = 12;
const ACTIONABLE_PUBLIC_STATUS_PATTERN =
  /error|failed|failure|denied|blocked|forbidden|unauthori[sz]ed|cancel/i;

function stableTextLogicalId(
  data: EventData,
  messageId: string | undefined,
  depth: number,
  agentId: string | undefined,
  segmentOrdinal: number,
): string {
  const owner = data.message_id || messageId || "assistant";
  return `${owner}:text:${segmentOrdinal}:${depth}:${agentId || "root"}`;
}

function rootTextSegmentCount(parts: MessagePart[]): number {
  return parts.filter((part) => part.type === "text" && !part.depth).length;
}

export function normalizeMessageTextLogicalIds(
  message: Message,
  rootTextOwnerId: string = message.id,
): Message {
  const textSegmentCounts = new Map<string, number>();
  let changed = false;

  const normalizeParts = (
    parts: MessagePart[],
    inheritedDepth = 0,
    inheritedAgentId = "root",
  ): MessagePart[] =>
    parts.map((part) => {
      if (part.type === "text") {
        const depth = part.depth ?? inheritedDepth;
        const agentId = part.agent_id || inheritedAgentId;
        const scope = `${depth}:${agentId}`;
        const segmentOrdinal = textSegmentCounts.get(scope) ?? 0;
        textSegmentCounts.set(scope, segmentOrdinal + 1);
        const fallbackLogicalId =
          `${message.id}:text:${segmentOrdinal}:${depth}:${agentId}`;
        const logicalId =
          `${rootTextOwnerId}:text:${segmentOrdinal}:${depth}:${agentId}`;
        if (
          part.logical_id &&
          (part.logical_id !== fallbackLogicalId || part.logical_id === logicalId)
        ) {
          return part;
        }
        changed = true;
        return {
          ...part,
          logical_id: logicalId,
        };
      }
      if (part.type === "subagent" && part.parts?.length) {
        const normalizedParts = normalizeParts(
          part.parts,
          part.depth + 1,
          part.agent_id,
        );
        if (
          normalizedParts.some(
            (nestedPart, index) => nestedPart !== part.parts?.[index],
          )
        ) {
          changed = true;
          return { ...part, parts: normalizedParts };
        }
      }
      return part;
    });

  const parts = normalizeParts(message.parts || []);
  return changed ? { ...message, parts } : message;
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
        result.parts = replaceAssistantTextWithFinal(
          parts,
          chunkContent,
          stableTextLogicalId(
            data,
            messageId,
            depth,
            agentId,
            rootTextSegmentCount(parts),
          ),
        );
        result.content = chunkContent;
        break;
      }

      if (depth > 0) {
        const textPart = {
          type: "text" as const,
          content: chunkContent,
          logical_id: stableTextLogicalId(
            data,
            messageId,
            depth,
            agentId,
            0,
          ),
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
            logical_id:
              lastPart.logical_id ||
              stableTextLogicalId(
                data,
                messageId,
                depth,
                agentId,
                Math.max(0, rootTextSegmentCount(parts) - 1),
              ),
          };
        } else {
          newParts.push({
            type: "text" as const,
            content: chunkContent,
            logical_id: stableTextLogicalId(
              data,
              messageId,
              depth,
              agentId,
              rootTextSegmentCount(parts),
            ),
          });
        }
        result.parts = newParts;
        result.content = content + chunkContent;
      }
      break;
    }

    // ---- Controlled terminal detail ----

    case "final_detail": {
      // Terminal detail is a fixed-code presentation contract. Never render
      // the backend-provided message itself: an unknown code, foreign version,
      // or mismatched kind fails closed, and useful partial assistant text
      // remains intact.
      if (data.projection_version !== CHAT_PUBLIC_PROJECTION_VERSION) break;
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
        run_reference:
          detailCode === "terminal_reconciliation_failed"
            ? publicTerminalRunReference(data.run_id)
            : undefined,
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

    case "tool_permission_card": {
      const permissionCard = createToolPermissionCardPart(data);
      if (permissionCard) {
        result.parts =
          permissionCard.status === "decided" || permissionCard.decision
            ? applyToolPermissionDecisionPart(parts, permissionCard)
            : upsertToolPermissionPart(parts, permissionCard);
      }
      break;
    }

    case "run_event": {
      const executionKind = String(data.event_type || "");
      if (PUBLIC_EXECUTION_EVENT_TYPES.has(executionKind as never)) {
        const executionPart = createExecutionTimelinePart(executionKind, data);
        if (executionPart) {
          result.parts = upsertPublicExecutionStep(parts, executionPart);
        }
        break;
      }
      if (data.event_type === "tool_permission_card") {
        const permissionCard = createToolPermissionCardPart(data);
        if (permissionCard) {
          result.parts =
            permissionCard.status === "decided" || permissionCard.decision
              ? applyToolPermissionDecisionPart(parts, permissionCard)
              : upsertToolPermissionPart(parts, permissionCard);
          break;
        }
      }
      if (data.event_type === "tool_permission_requested") {
        // Public persisted history projects permission requests as a
        // controlled card.  Live legacy frames may still carry the older
        // direct payload, so accept both without reintroducing action rights.
        const permissionPart =
          createToolPermissionCardPart(data) ??
          createToolPermissionRequestedPart(data);
        if (permissionPart) {
          result.parts = upsertToolPermissionPart(parts, permissionPart);
          break;
        }
      }
      if (data.event_type === "tool_permission_decided") {
        const permissionDecision = createToolPermissionDecidedPart(data);
        if (permissionDecision) {
          result.parts = applyToolPermissionDecisionPart(
            parts,
            permissionDecision,
          );
          break;
        }
      }
      if (data.event_type === "tool_permission_terminalized") {
        const permissionTerminal =
          createToolPermissionCardPart(data) ??
          createToolPermissionTerminalizedPart(data);
        if (permissionTerminal) {
          result.parts = upsertToolPermissionPart(parts, permissionTerminal);
          break;
        }
      }
      if (data.event_type === "public_tool_activity") {
        const toolPart = createPublicToolPart(data);
        if (toolPart) {
          result.parts = upsertPublicToolPart(parts, toolPart);
          break;
        }
      }
      if (data.event_type === "public_subagent_activity") {
        const subagentPart = createPublicSubagentPart(data, depth, parts);

        if (subagentPart) {
          result.parts = upsertPublicSubagentPart(parts, subagentPart);
          break;
        }
      }
      if (data.event_type === "agent_public_progress") {
        const progress = projectPublicAgentProgress(data);
        if (progress) {
          result.parts = upsertPublicExecutionStep(parts, progress);
        }
        break;
      }
      if (
        data.event_type === "public_activity" &&
        typeof data.stage === "string" &&
        data.stage.startsWith("thinking")
      ) {
        const thinking = projectPublicThinkingActivity(data, isStreaming);
        if (thinking) {
          result.parts = upsertPublicThinkingActivity(parts, thinking);
        }
        break;
      }
      if (!shouldProjectRunStatus(data)) {
        break;
      }
      const eventId = String(data.event_id || data.id || "");
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
      const artifactId = String(data.artifact_id || data.id || "");
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

function createPublicToolPart(data: EventData): Extract<MessagePart, { type: "tool" }> | null {
  const operationId = typeof data.operation_id === "string" ? data.operation_id : "";
  const displayName = typeof data.display_name === "string" ? data.display_name : "";
  const category = typeof data.category === "string" ? data.category : "";
  const status = typeof data.status === "string" ? data.status : "";
  if (
    !operationId ||
    !displayName ||
    !category ||
    !status ||
    !["started", "completed", "failed", "denied"].includes(status)
  ) return null;
  const inputSummary =
    typeof data.input_summary === "string" ? data.input_summary : undefined;
  const failed = status === "failed" || status === "denied";
  return {
    type: "tool",
    id: operationId,
    name: displayName,
    args: { category, ...(inputSummary ? { summary: inputSummary } : {}) },
    result: typeof data.result_summary === "string" ? data.result_summary : undefined,
    status: status as "started" | "completed" | "failed" | "denied",
    success: failed ? false : status === "completed" ? true : undefined,
    error: undefined,
    isPending: status === "started",
    cancelled: false,
    depth: typeof data.depth === "number" ? data.depth : 0,
    agent_id: typeof data.agent_id === "string" ? data.agent_id : undefined,
    public_operation_id: operationId,
    public_category: category,
    public_input_summary: inputSummary,
    duration_ms: typeof data.duration_ms === "number" ? data.duration_ms : undefined,
    evidence_refs: Array.isArray(data.evidence_refs) ? data.evidence_refs : undefined,
    artifact_refs: Array.isArray(data.artifact_refs) ? data.artifact_refs : undefined,
    event_id: typeof data.event_id === "string" ? data.event_id : undefined,
    causation_event_id: data.causation_event_id ?? null,
  };
}

function upsertPublicToolPart(
  parts: MessagePart[],
  next: Extract<MessagePart, { type: "tool" }>,
): MessagePart[] {
  const index = parts.findIndex(
    (part) => part.type === "tool" && part.public_operation_id === next.public_operation_id,
  );
  if (index < 0) return [...parts, next];
  const updated = [...parts];
  const current = updated[index];
  if (current?.type !== "tool") return parts;
  updated[index] = {
    ...current,
    ...next,
    args: next.public_input_summary ? next.args : current.args,
    result: next.result ?? current.result,
    public_input_summary:
      next.public_input_summary ?? current.public_input_summary,
  };
  return updated;
}

function acceptedSubagentForEvent(
  parts: MessagePart[],
  parentEventId: string,
): Extract<MessagePart, { type: "subagent" }> | undefined {
  for (const part of parts) {
    if (
      part.type === "subagent" &&
      (part.origin_event_id === parentEventId || part.event_id === parentEventId)
    ) {
      return part;
    }
    if (part.type === "subagent" && part.parts) {
      const nested = acceptedSubagentForEvent(part.parts, parentEventId);
      if (nested) return nested;
    }
  }
  return undefined;
}

const PUBLIC_SUBAGENT_CATEGORIES = new Set([
  "skill",
  "mcp",
  "read",
  "write",
  "edit",
  "search",
  "execute",
]);

function boundedSubagentDuration(value: unknown): number | undefined {
  return typeof value === "number" &&
    Number.isSafeInteger(value) &&
    value >= 0 &&
    value <= 86_400_000
    ? value
    : undefined;
}

function boundedSubagentProgress(value: unknown): number | undefined {
  return typeof value === "number" &&
    Number.isSafeInteger(value) &&
    value >= 0 &&
    value <= 100
    ? value
    : undefined;
}

function boundedSubagentCategory(value: unknown): string | undefined {
  return typeof value === "string" && PUBLIC_SUBAGENT_CATEGORIES.has(value)
    ? value
    : undefined;
}

function createPublicSubagentPart(
  data: EventData,
  depth: number,
  parts: MessagePart[],
): Extract<MessagePart, { type: "subagent" }> | null {
  const agentId = typeof data.subagent_id === "string" ? data.subagent_id : "";
  const agentName = typeof data.display_name === "string" ? data.display_name : "";
  const status = typeof data.status === "string" ? data.status : "";
  if (!agentId || !agentName || !status) return null;
  const terminal = status === "completed" || status === "failed" || status === "cancelled";
  const causationEventId = typeof data.causation_event_id === "string"
    ? data.causation_event_id
    : null;
  const parent = causationEventId
    ? acceptedSubagentForEvent(parts, causationEventId)
    : undefined;
  const parentAgentId = parent?.public_operation_id || parent?.agent_id;
  const durationMs = boundedSubagentDuration(data.duration_ms);
  const progressPercent = boundedSubagentProgress(data.progress_percent);
  const currentCategory = boundedSubagentCategory(data.current_category);
  return {
    type: "subagent",
    agent_id: agentId,
    agent_name: agentName,
    input: "",
    isPending: !terminal,
    status: status === "progress" ? "running" : status === "started" ? "running" : status === "completed" ? "complete" : status === "cancelled" ? "cancelled" : "error",
    depth: parent
      ? parent.depth + 1
      : typeof data.depth === "number"
        ? data.depth
        : depth,
    parts: [],
    startedAt: typeof data.timestamp === "string" ? Date.parse(data.timestamp) : undefined,
    completedAt: terminal && typeof data.timestamp === "string" ? Date.parse(data.timestamp) : undefined,
    error: status === "failed" && typeof data.failure_category === "string" ? data.failure_category : undefined,
    parent_agent_id: parentAgentId,
    public_operation_id: agentId,
    duration_ms: durationMs,
    progress_percent: progressPercent,
    current_category: currentCategory,
    origin_event_id: typeof data.event_id === "string" ? data.event_id : undefined,
    event_id: typeof data.event_id === "string" ? data.event_id : undefined,
    causation_event_id: causationEventId,
  };
}

function updateSubagentTree(
  parts: MessagePart[],
  next: Extract<MessagePart, { type: "subagent" }>,
): { parts: MessagePart[]; found: boolean } {
  let found = false;
  const updated = parts.map((part) => {
    if (part.type !== "subagent") return part;
    if (
      part.agent_id === next.agent_id &&
      part.public_operation_id === next.public_operation_id
    ) {
      found = true;
      return {
        ...part,
        ...next,
        depth: part.depth,
        parent_agent_id: part.parent_agent_id,
        origin_event_id: part.origin_event_id,
        causation_event_id: part.causation_event_id,
        parts: part.parts ?? [],
        startedAt: part.startedAt ?? next.startedAt,
      };
    }
    if (part.parts) {
      const nested = updateSubagentTree(part.parts, next);
      if (nested.found) {
        found = true;
        return { ...part, parts: nested.parts };
      }
    }
    return part;
  });
  return { parts: updated, found };
}

function appendSubagentToParent(
  parts: MessagePart[],
  next: Extract<MessagePart, { type: "subagent" }>,
): { parts: MessagePart[]; found: boolean } {
  let found = false;
  const updated = parts.map((part) => {
    if (part.type !== "subagent") return part;
    if (part.public_operation_id === next.parent_agent_id) {
      found = true;
      return { ...part, parts: [...(part.parts || []), next] };
    }
    if (part.parts) {
      const nested = appendSubagentToParent(part.parts, next);
      if (nested.found) {
        found = true;
        return { ...part, parts: nested.parts };
      }
    }
    return part;
  });
  return { parts: updated, found };
}

function upsertPublicSubagentPart(
  parts: MessagePart[],
  next: Extract<MessagePart, { type: "subagent" }>,
): MessagePart[] {
  const updated = updateSubagentTree(parts, next);
  if (updated.found) return updated.parts;
  if (next.parent_agent_id) {
    const nested = appendSubagentToParent(parts, next);
    if (nested.found) return nested.parts;
  }
  return [...parts, next];
}



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
    stage:
      publicEvent.schema_version === PUBLIC_EXECUTION_EVENT_V2_SCHEMA_VERSION
        ? publicEvent.stage
        : undefined,
    title: undefined,
    summary: undefined,
    status: publicEvent.status,
    progress: publicEvent.progress,
    safe_file_name: safePublicExecutionFileName(
      publicEvent.safe_file_name ?? null,
    ),
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

type ValidPublicExecutionEvent = EventData & {
  sequence: number;
  step_id: string;
  kind: ExecutionTimelinePart["kind"];
  status: ExecutionTimelinePart["status"];
  progress: ExecutionTimelinePart["progress"];
  safe_file_name?: string | null;
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
  const fields =
    source.schema_version === "ai-platform.public-execution-event.v2"
      ? PUBLIC_EXECUTION_V2_FIELDS
      : source.schema_version === "ai-platform.public-execution-event.v1"
        ? PUBLIC_EXECUTION_V1_FIELDS
        : null;
  if (fields === null) return null;
  const envelopeFields = new Set([...fields, "event_type", "timestamp"]);
  if (
    source.event_type !== eventType ||
    (source.timestamp !== undefined && typeof source.timestamp !== "string") ||
    !Object.keys(source).every((key) => envelopeFields.has(key))
  ) {
    return null;
  }
  const normalized = Object.fromEntries(
    [...fields]
      .filter((key) => Object.hasOwn(source, key))
      .map((key) => [key, source[key]]),
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
  logicalId?: string,
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
  return replacedText
    ? converged
    : [
        ...converged,
        { type: "text", content, logical_id: logicalId },
      ];
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

function stringField(
  source: Record<string, unknown>,
  key: string,
): string | undefined {
  const value = source[key];
  return typeof value === "string" && value.trim() ? value : undefined;
}

function toolPermissionDecision(
  value: unknown,
): ToolPermissionDecision | undefined {
  return value === "allow_once" || value === "allow_for_run" || value === "deny"
    ? value
    : undefined;
}

const TOOL_PERMISSION_TERMINAL_STATUSES = new Set<ToolPermissionStatus>([
  "expired",
  "cancelled",
  "failed",
  "invalidated",
]);

function toolPermissionStatus(value: unknown): ToolPermissionStatus | undefined {
  return value === "pending" ||
    value === "decided" ||
    value === "expired" ||
    value === "cancelled" ||
    value === "failed" ||
    value === "invalidated"
    ? value
    : undefined;
}

function createToolPermissionRequestedPart(
  data: EventData,
): ToolPermissionPart | null {
  const payload = asRecord(data.payload);
  const eventId = String(data.event_id || data.id || "");
  const requestId = stringField(payload, "permission_request_id");
  const runId = data.run_id || stringField(payload, "run_id");
  const toolId = stringField(payload, "tool_id");
  const toolCallId = stringField(payload, "tool_call_id");
  if (!eventId || !requestId || !runId || !toolId || !toolCallId) {
    return null;
  }
  return {
    type: "tool_permission",
    event_id: eventId,
    run_id: runId,
    permission_request_id: requestId,
    tool_id: toolId,
    tool_call_id: toolCallId,
    risk_level: stringField(payload, "risk_level") || "low",
    write_capable: payload.write_capable === true,
    status: "pending",
    sequence: typeof data.sequence === "number" ? data.sequence : undefined,
    created_at: data.created_at || data.timestamp,
  };
}

function createToolPermissionDecidedPart(
  data: EventData,
): ToolPermissionPartWithMergeHints | null {
  const payload = asRecord(data.payload);
  const eventId = String(data.event_id || data.id || "");
  const requestId = stringField(payload, "permission_request_id");
  const runId = data.run_id || stringField(payload, "run_id");
  const toolId = stringField(payload, "tool_id");
  const toolCallId = stringField(payload, "tool_call_id");
  const decision = toolPermissionDecision(payload.decision);
  const riskLevel = stringField(payload, "risk_level");
  const hasWriteCapable = typeof payload.write_capable === "boolean";
  if (!eventId || !requestId || !runId || !toolId || !toolCallId || !decision) {
    return null;
  }
  return {
    type: "tool_permission",
    event_id: eventId,
    decided_event_id: eventId,
    run_id: runId,
    permission_request_id: requestId,
    tool_id: toolId,
    tool_call_id: toolCallId,
    risk_level: riskLevel || "low",
    write_capable: hasWriteCapable ? payload.write_capable === true : false,
    status: "decided",
    decision,
    sequence: typeof data.sequence === "number" ? data.sequence : undefined,
    decided_at: data.created_at || data.timestamp,
    risk_level_from_event: Boolean(riskLevel),
    write_capable_from_event: hasWriteCapable,
  };
}

function createToolPermissionCardPart(
  data: EventData,
): ToolPermissionPart | null {
  const payload = asRecord(data.payload);
  const card = asRecord(payload.tool_permission_card || data.tool_permission_card);
  const eventId = String(data.event_id || data.id || "");
  const requestId = stringField(card, "permission_request_id");
  const runId = data.run_id || stringField(card, "run_id");
  const toolId = stringField(card, "tool_id");
  const toolCallId = stringField(card, "tool_call_id");
  const decision = toolPermissionDecision(card.decision);
  const status = toolPermissionStatus(card.status) ?? (decision ? "decided" : "pending");
  if (!eventId || !requestId || !runId || !toolId || !toolCallId) {
    return null;
  }
  if (status === "decided" && !decision) {
    return null;
  }
  return {
    type: "tool_permission",
    event_id: eventId,
    decided_event_id: status === "decided" ? eventId : undefined,
    run_id: runId,
    permission_request_id: requestId,
    tool_id: toolId,
    tool_call_id: toolCallId,
    risk_level: stringField(card, "risk_level") || "low",
    write_capable: card.write_capable === true,
    status,
    decision,
    sequence: typeof data.sequence === "number" ? data.sequence : undefined,
    created_at:
      status === "pending"
        ? stringField(card, "created_at") || data.created_at || data.timestamp
        : undefined,
    decided_at:
      status === "decided"
        ? stringField(card, "decided_at") || data.created_at || data.timestamp
        : undefined,
  };
}

function createToolPermissionTerminalizedPart(
  data: EventData,
): ToolPermissionPart | null {
  const payload = asRecord(data.payload);
  const eventId = String(data.event_id || data.id || "");
  const requestId = stringField(payload, "permission_request_id");
  const runId = data.run_id || stringField(payload, "run_id");
  const toolId = stringField(payload, "tool_id");
  const toolCallId = stringField(payload, "tool_call_id");
  const status = toolPermissionStatus(payload.status);
  if (
    !eventId ||
    !requestId ||
    !runId ||
    !toolId ||
    !toolCallId ||
    !status ||
    !TOOL_PERMISSION_TERMINAL_STATUSES.has(status)
  ) {
    return null;
  }
  return {
    type: "tool_permission",
    event_id: eventId,
    run_id: runId,
    permission_request_id: requestId,
    tool_id: toolId,
    tool_call_id: toolCallId,
    risk_level: stringField(payload, "risk_level") || "low",
    write_capable: payload.write_capable === true,
    status,
    sequence: typeof data.sequence === "number" ? data.sequence : undefined,
  };
}

function upsertToolPermissionPart(
  parts: MessagePart[],
  toolPermissionPart: ToolPermissionPart,
): MessagePart[] {
  return parts.some(
    (p) =>
      p.type === "tool_permission" &&
      p.permission_request_id === toolPermissionPart.permission_request_id,
  )
    ? parts.map((p) =>
        p.type === "tool_permission" &&
        p.permission_request_id === toolPermissionPart.permission_request_id
          ? p.status === "decided" && toolPermissionPart.status === "pending"
            ? p
            : TOOL_PERMISSION_TERMINAL_STATUSES.has(p.status) &&
                !TOOL_PERMISSION_TERMINAL_STATUSES.has(toolPermissionPart.status)
              ? p
            : { ...p, ...toolPermissionPart }
          : p,
      )
    : [...parts, toolPermissionPart];
}

function applyToolPermissionDecisionPart(
  parts: MessagePart[],
  decisionPart: ToolPermissionPart,
): MessagePart[] {
  const decisionHints = decisionPart as ToolPermissionPartWithMergeHints;
  const riskLevelFromEvent = decisionHints.risk_level_from_event !== false;
  const writeCapableFromEvent = decisionHints.write_capable_from_event !== false;
  const normalizedDecisionPart: ToolPermissionPart = {
    ...decisionPart,
  };
  delete (normalizedDecisionPart as ToolPermissionPartWithMergeHints)
    .risk_level_from_event;
  delete (normalizedDecisionPart as ToolPermissionPartWithMergeHints)
    .write_capable_from_event;

  return parts.some(
    (p) =>
      p.type === "tool_permission" &&
      p.permission_request_id === decisionPart.permission_request_id,
  )
    ? parts.map((p) =>
        p.type === "tool_permission" &&
        p.permission_request_id === decisionPart.permission_request_id
          ? TOOL_PERMISSION_TERMINAL_STATUSES.has(p.status) ||
              (typeof p.sequence === "number" &&
                typeof decisionPart.sequence === "number" &&
                decisionPart.sequence < p.sequence)
            ? p
            : {
                ...p,
                run_id: decisionPart.run_id || p.run_id,
                tool_id: decisionPart.tool_id || p.tool_id,
                tool_call_id: decisionPart.tool_call_id || p.tool_call_id,
                risk_level: riskLevelFromEvent ? decisionPart.risk_level : p.risk_level,
                write_capable: writeCapableFromEvent
                  ? decisionPart.write_capable
                  : p.write_capable,
                decided_event_id: decisionPart.decided_event_id,
                status: decisionPart.status,
                decision: decisionPart.decision,
                sequence: decisionPart.sequence,
                decided_at: decisionPart.decided_at,
              }
          : p,
      )
    : [...parts, normalizedDecisionPart];
}
