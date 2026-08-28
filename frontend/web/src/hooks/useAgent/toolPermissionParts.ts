import type {
  MessagePart,
  ToolPermissionDecision,
  ToolPermissionPart,
  ToolPermissionStatus,
} from "../../types";
import type { EventData } from "./types";

type ToolPermissionPartWithMergeHints = ToolPermissionPart & {
  risk_level_from_event?: boolean;
  write_capable_from_event?: boolean;
};

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

export function createToolPermissionRequestedPart(
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

export function createToolPermissionDecidedPart(
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

export function createToolPermissionCardPart(
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

export function createToolPermissionTerminalizedPart(
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

export function upsertToolPermissionPart(
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

export function applyToolPermissionDecisionPart(
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
