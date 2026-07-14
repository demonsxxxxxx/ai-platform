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
  ToolPermissionDecision,
  ToolPermissionPart,
  ArtifactPart,
  TodoPart,
  SummaryPart,
} from "../../types";
import i18n from "../../i18n";
import { translateBackendError } from "../../utils/backendErrors";
import type { EventData, SubagentStackItem } from "./types";
import {
  addPartToDepth,
  createSubagentPart,
  createThinkingPart,
  createToolPart,
  updateSubagentResult,
  updateToolResultInDepth,
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

const SENSITIVE_TOOL_PAYLOAD_KEYS = new Set([
  "request_payload",
  "decision_payload",
  "private_payload",
  "privatePayload",
  "executor_private_payload",
  "executorPrivatePayload",
  "storage_key",
  "storageKey",
  "runtime_path",
  "runtimePath",
  "work_dir",
  "workDir",
  "sandbox_workdir",
  "sandboxWorkdir",
  "command_sha256",
  "commandSha256",
  "resource_limits",
  "resourceLimits",
  "used_skills_source",
  "usedSkillsSource",
]);

const SENSITIVE_TOOL_PAYLOAD_KEY_PATTERNS = [
  /private.?payload/i,
  /executor.?private/i,
  /storage.?key/i,
  /runtime.?path/i,
  /work.?dir/i,
  /sandbox.?work/i,
  /command.?sha/i,
  /resource.?limits/i,
  /used.?skills.?source/i,
];

const SENSITIVE_TOOL_PAYLOAD_VALUE_PATTERNS = [
  /tenants\/[^/\s]+\/private/i,
  /\.claude\/(?:runs|skills)/i,
  /\/tmp\/tenants\//i,
  /\/workspace\/\.claude\//i,
  /storage[_-]?key=/i,
];

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

function isSensitiveToolPayloadKey(key: string): boolean {
  return (
    SENSITIVE_TOOL_PAYLOAD_KEYS.has(key) ||
    SENSITIVE_TOOL_PAYLOAD_KEY_PATTERNS.some((pattern) => pattern.test(key))
  );
}

function isSensitiveToolPayloadString(value: string): boolean {
  return SENSITIVE_TOOL_PAYLOAD_VALUE_PATTERNS.some((pattern) =>
    pattern.test(value),
  );
}

function sanitizeToolPayloadValue(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value
      .map((item) => sanitizeToolPayloadValue(item))
      .filter((item) => item !== undefined);
  }

  if (value && typeof value === "object") {
    const output: Record<string, unknown> = {};
    for (const [key, childValue] of Object.entries(
      value as Record<string, unknown>,
    )) {
      if (isSensitiveToolPayloadKey(key)) continue;
      const sanitizedValue = sanitizeToolPayloadValue(childValue);
      if (sanitizedValue !== undefined) {
        output[key] = sanitizedValue;
      }
    }
    return output;
  }

  if (typeof value === "string" && isSensitiveToolPayloadString(value)) {
    return "[redacted]";
  }

  return value;
}

function sanitizeToolArgs(args: Record<string, unknown>): Record<string, unknown> {
  const sanitized = sanitizeToolPayloadValue(args);
  return sanitized && typeof sanitized === "object" && !Array.isArray(sanitized)
    ? (sanitized as Record<string, unknown>)
    : {};
}

function sanitizeToolResult(
  result: string | Record<string, unknown>,
): string | Record<string, unknown> {
  const sanitized = sanitizeToolPayloadValue(result);
  if (typeof sanitized === "string") {
    return sanitized;
  }
  if (sanitized && typeof sanitized === "object" && !Array.isArray(sanitized)) {
    return sanitized as Record<string, unknown>;
  }
  return "";
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
        data.error,
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
      const chunkContent = data.content || "";
      if (!chunkContent) break;

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
      // This is deliberately not the generic `error` SSE envelope.  The
      // backend may only send a code-only failed detail; unknown detail
      // shapes fail closed instead of exposing executor text.
      if (
        data.detail_kind !== "failed" ||
        data.detail_code !== "run_failed"
      ) {
        break;
      }
      result.content = i18n.t("chat.runTerminal.failed");
      break;
    }

    // ---- Tool events ----

    case "tool:start": {
      const toolCallId = data.tool_call_id as string | undefined;
      const safeArgs = sanitizeToolArgs(data.args || {});
      const toolCall: ToolCall = {
        id: toolCallId,
        name: data.tool || "",
        args: safeArgs,
      };
      const toolPart = createToolPart(
        data.tool || "",
        safeArgs,
        depth,
        agentId,
        toolCallId,
      );

      if (depth > 0) {
        result.parts = addPartToDepth(
          parts,
          toolPart,
          depth,
          subagentStack,
          agentId,
          messageId,
        );
      } else {
        result.parts = [...parts, toolPart];
        result.toolCalls = [...toolCalls, toolCall];
      }
      break;
    }

    case "tool:result": {
      const toolCallId = data.tool_call_id as string | undefined;
      const toolName = data.tool || "";
      const isSuccess = data.success !== false;
      const errorMsg = data.error as string | undefined;
      const resultContent = sanitizeToolResult(data.result || "");

      if (depth > 0 || toolCallId) {
        result.parts = updateToolResultInDepth(
          parts,
          toolCallId || "",
          resultContent,
          isSuccess,
          errorMsg,
          depth,
          agentId,
        );
      } else {
        let updated = false;
        const newParts = parts.map((p) => {
          if (
            p.type === "tool" &&
            p.name === toolName &&
            p.isPending &&
            !updated
          ) {
            updated = true;
            return {
              ...p,
              result: resultContent,
              success: isSuccess,
              error: errorMsg,
              isPending: false,
            };
          }
          return p;
        });
        result.parts = newParts;
        result.toolResult = {
          id: toolCallId,
          name: toolName,
          result: resultContent,
          success: isSuccess,
        };
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
        error: data.error,
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

/** Replace an existing platform run event projection by event id. */
function upsertRunStatusPart(
  parts: MessagePart[],
  runStatusPart: RunStatusPart,
): MessagePart[] {
  return parts.some(
    (p) => p.type === "run_status" && p.event_id === runStatusPart.event_id,
  )
    ? parts.map((p) =>
        p.type === "run_status" && p.event_id === runStatusPart.event_id
          ? runStatusPart
          : p,
      )
    : [...parts, runStatusPart];
}

function shouldProjectRunStatus(data: EventData): boolean {
  const payload = asRecord(data.payload);
  if (payload.visible_to_user === false || payload.visibleToUser === false) {
    return false;
  }
  if (data.severity === "error" || data.severity === "warning") {
    return true;
  }
  const eventType = String(data.event_type || data.type || "").toLowerCase();
  return (
    eventType.includes("error") ||
    eventType.includes("failed") ||
    eventType.includes("denied") ||
    eventType.includes("blocked")
  );
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
  const status =
    stringField(card, "status") === "decided" || decision ? "decided" : "pending";
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
          ? {
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
