import {
  PUBLIC_STREAM_EVENT_TYPES,
  type PublicRunStreamEventV4,
} from "../../../generated/publicRunStreamV4";
import type { StreamEvent } from "../../../hooks/useAgent/types";

export type V4ApplicationEventType = (typeof PUBLIC_STREAM_EVENT_TYPES)[number];
export type V4ControlEventType =
  | "stream.open"
  | "stream.heartbeat"
  | "stream.gap"
  | "stream.end";
export type V4EventType = V4ApplicationEventType | V4ControlEventType;

export interface V4SseFrame {
  eventHeader: string;
  transportCursor: string;
  generation?: number;
  value: unknown;
}

export interface V4PublicEvent {
  readonly event: PublicRunStreamEventV4;
  readonly eventId: string;
  readonly transportCursor: string;
  readonly runId: string;
  readonly messageId: string | null;
  readonly sequence: number | null;
  readonly eventType: V4EventType;
  readonly streamIncarnation: number;
  readonly generation?: number;
  readonly emittedAt: string;
  readonly semanticKey: string;
}

export interface V4AdapterBinding {
  runId: string;
  generation?: number;
  streamIncarnation?: number | null;
}

const APPLICATION_EVENT_TYPES = new Set<string>(PUBLIC_STREAM_EVENT_TYPES);
const CONTROL_EVENT_TYPES = new Set<V4ControlEventType>([
  "stream.open",
  "stream.heartbeat",
  "stream.gap",
  "stream.end",
]);

const PAYLOAD_KEYS: Record<string, readonly string[]> = {
  "message.started": [],
  "message.delta": ["delta"],
  "message.completed": ["content"],
  "thinking.started": [],
  "thinking.completed": [],
  "model.completed": ["duration_ms", "turn_count", "stop_category"],
  "tool.started": ["operation_id", "category", "display_name", "input_summary", "evidence_refs"],
  "tool.completed": ["operation_id", "category", "display_name", "duration_ms", "result_summary", "evidence_refs", "artifact_refs"],
  "tool.failed": ["operation_id", "category", "display_name", "duration_ms", "failure_category", "evidence_refs"],
  "tool.denied": ["operation_id", "category", "display_name", "denial_code"],
  "subagent.started": ["subagent_id", "display_name"],
  "subagent.progress": ["subagent_id", "display_name", "duration_ms", "current_category", "progress_percent"],
  "subagent.completed": ["subagent_id", "display_name", "duration_ms"],
  "subagent.failed": ["subagent_id", "display_name", "duration_ms", "failure_category"],
  "subagent.cancelled": ["subagent_id", "display_name", "duration_ms", "reason_code"],
  "artifact.created": ["artifact_id", "filename", "media_type", "size_bytes", "status", "evidence_ref"],
  "artifact.ready": ["artifact_id", "filename", "media_type", "size_bytes", "status", "evidence_ref"],
  "artifact.failed": ["artifact_id", "status", "failure_category", "filename", "media_type"],
  "policy.checking": ["decision_id", "category", "display_name"],
  "policy.allowed": ["decision_id", "category", "display_name", "decision_code"],
  "policy.denied": ["decision_id", "category", "display_name", "decision_code"],
  "run.cancel_requested": ["source"],
  "run.succeeded": ["terminal_event_id", "hydrate_required"],
  "run.cancelled": ["terminal_event_id", "hydrate_required", "reason_code"],
  "run.failed": ["terminal_event_id", "hydrate_required", "projection_version", "code", "default_message", "detail"],
  "stream.open": ["design_id"],
  "stream.heartbeat": ["status"],
  "stream.gap": ["reason", "recovery", "requested_event_id", "requested_stream_incarnation", "current_stream_incarnation", "earliest_available_event_id", "latest_available_event_id"],
  "stream.end": ["terminal_event_id"],
};

const REQUIRED_PAYLOAD_KEYS: Record<string, readonly string[]> = {
  "message.delta": ["delta"],
  "message.completed": ["content"],
  "model.completed": ["duration_ms", "turn_count", "stop_category"],
  "tool.started": ["operation_id", "category", "display_name"],
  "tool.completed": ["operation_id", "category", "display_name", "duration_ms"],
  "tool.failed": ["operation_id", "category", "display_name", "duration_ms", "failure_category"],
  "tool.denied": ["operation_id", "category", "display_name", "denial_code"],
  "subagent.started": ["subagent_id", "display_name"],
  "subagent.progress": ["subagent_id", "display_name", "duration_ms", "current_category"],
  "subagent.completed": ["subagent_id", "display_name", "duration_ms"],
  "subagent.failed": ["subagent_id", "display_name", "duration_ms", "failure_category"],
  "subagent.cancelled": ["subagent_id", "display_name", "duration_ms", "reason_code"],
  "artifact.created": ["artifact_id", "filename", "media_type", "size_bytes", "status"],
  "artifact.ready": ["artifact_id", "filename", "media_type", "size_bytes", "status"],
  "artifact.failed": ["artifact_id", "status", "failure_category"],
  "policy.checking": ["decision_id", "category", "display_name"],
  "policy.allowed": ["decision_id", "category", "display_name", "decision_code"],
  "policy.denied": ["decision_id", "category", "display_name", "decision_code"],
  "run.cancel_requested": ["source"],
  "run.succeeded": ["terminal_event_id", "hydrate_required"],
  "run.cancelled": ["terminal_event_id", "hydrate_required", "reason_code"],
  "run.failed": ["terminal_event_id", "hydrate_required", "projection_version", "code", "default_message", "detail"],
  "stream.open": ["design_id"],
  "stream.heartbeat": ["status"],
  "stream.gap": ["reason", "recovery", "requested_event_id", "requested_stream_incarnation", "current_stream_incarnation", "earliest_available_event_id", "latest_available_event_id"],
  "stream.end": ["terminal_event_id"],
};

const APPLICATION_KEYS = [
  "schema", "event_id", "run_id", "message_id", "seq", "event_type",
  "stream_incarnation", "replayable", "trace_ref", "causation_event_id",
  "emitted_at", "payload",
] as const;
const CONTROL_KEYS = APPLICATION_KEYS;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasOnlyKeys(value: Record<string, unknown>, allowed: readonly string[]): boolean {
  const allowedSet = new Set(allowed);
  return Object.keys(value).every((key) => allowedSet.has(key));
}

function nonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function safeInteger(value: unknown, minimum = 0, maximum = Number.MAX_SAFE_INTEGER): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= minimum && value <= maximum;
}

const SAFE_REF_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,255}$/;
const TOOL_CATEGORIES = new Set(["skill", "mcp", "read", "write", "edit", "search", "execute"]);
const PAYLOAD_ENUMS: Record<string, ReadonlySet<string>> = {
  category: TOOL_CATEGORIES,
  current_category: TOOL_CATEGORIES,
  stop_category: new Set(["completed", "max_turns", "cancelled", "failed", "unknown"]),
  failure_category: new Set(["invalid_input", "not_found", "permission_denied", "timeout", "unavailable", "execution_failed", "subagent_failed", "artifact_failed"]),
  denial_code: new Set(["capability_not_authorized", "policy_denied"]),
  reason_code: new Set(["user_cancelled", "run_cancelled", "policy_cancelled", "timeout"]),
  source: new Set(["user", "system"]),
  status: new Set(["created", "ready", "failed", "queued", "running"]),
  reason: new Set(["retained_history_unavailable", "stream_missing", "stream_continuity_unproven", "stream_incarnation_mismatch"]),
  recovery: new Set(["reload_durable_state"]),
  decision_code: new Set(["allowed", "capability_not_authorized", "policy_denied"]),
  design_id: new Set(["ai-platform.redis-streams-sse-event-channel.v4"]),
  projection_version: new Set(["ai-platform.chat-public-projection.v1"]),
};
const EVENT_PAYLOAD_ENUMS: Record<string, ReadonlySet<string>> = {
  "artifact.created.status": new Set(["created"]),
  "artifact.ready.status": new Set(["ready"]),
  "artifact.failed.status": new Set(["failed"]),
  "stream.heartbeat.status": new Set(["queued", "running"]),
  "subagent.cancelled.reason_code": new Set(["user_cancelled", "run_cancelled", "timeout"]),
  "run.cancelled.reason_code": new Set(["user_cancelled", "policy_cancelled", "timeout"]),
  "policy.allowed.decision_code": new Set(["allowed"]),
  "policy.denied.decision_code": new Set(["capability_not_authorized", "policy_denied"]),
  "tool.failed.failure_category": new Set(["invalid_input", "not_found", "permission_denied", "timeout", "unavailable", "execution_failed"]),
  "subagent.failed.failure_category": new Set(["subagent_failed"]),
  "artifact.failed.failure_category": new Set(["artifact_failed", "unavailable"]),
};
const PAYLOAD_STRING_MAX: Record<string, number> = {
  delta: 8192,
  content: 262144,
  display_name: 128,
  input_summary: 512,
  result_summary: 2048,
  media_type: 128,
  default_message: 1024,
  detail: 2048,
  code: 128,
};
const NON_EMPTY_PAYLOAD_STRINGS = new Set(["delta", "display_name", "media_type", "code", "default_message"]);
const PAYLOAD_NUMBER_MAX: Record<string, number> = {
  duration_ms: 86400000,
  turn_count: 10000,
  progress_percent: 100,
  size_bytes: 1099511627776,
};

function payloadIsValid(eventType: string, payload: unknown): payload is Record<string, unknown> {
  if (!isRecord(payload) || Object.keys(payload).length > 64) return false;
  const allowed = PAYLOAD_KEYS[eventType];
  if (!allowed || !hasOnlyKeys(payload, allowed)) return false;
  for (const key of REQUIRED_PAYLOAD_KEYS[eventType] || []) {
    if (!(key in payload)) return false;
  }
  for (const [key, value] of Object.entries(payload)) {
    if (PAYLOAD_ENUMS[key] && (typeof value !== "string" || !(EVENT_PAYLOAD_ENUMS[`${eventType}.${key}`] || PAYLOAD_ENUMS[key]).has(value))) return false;
    if (key.endsWith("_refs")) {
      if (!Array.isArray(value) || value.length > 32 || new Set(value).size !== value.length || value.some((entry) => typeof entry !== "string" || !SAFE_REF_PATTERN.test(entry))) return false;
    }
    if (key === "event_id" || key === "terminal_event_id" || key === "operation_id" || key === "artifact_id" || key === "decision_id" || key === "subagent_id") {
      if (!nonEmptyString(value) || !SAFE_REF_PATTERN.test(value)) return false;
    }
    const stringMax = PAYLOAD_STRING_MAX[key];
    if (stringMax !== undefined && (typeof value !== "string" || value.length > stringMax || (NON_EMPTY_PAYLOAD_STRINGS.has(key) && value.length === 0))) return false;
    const numberMax = PAYLOAD_NUMBER_MAX[key];
    if (numberMax !== undefined && !safeInteger(value, 0, numberMax)) return false;
    if (key === "hydrate_required" && value !== true) return false;
    if ((key === "detail" || key === "evidence_ref" || key === "requested_event_id" || key === "earliest_available_event_id" || key === "latest_available_event_id" || key.endsWith("_stream_incarnation")) && value !== null && value !== undefined) {
      if (key.endsWith("_stream_incarnation")) {
        if (!safeInteger(value, 1)) return false;
      } else if (typeof value !== "string" || (key !== "detail" && !SAFE_REF_PATTERN.test(value))) return false;
    }
    if (key === "filename" && (typeof value !== "string" || value.length === 0 || value.length > 255 || /[/\\\\\\u0000-\\u001f\\u007f]/.test(value))) return false;
    if (key === "current_stream_incarnation" && !safeInteger(value, 1)) return false;
  }
  return true;
}

function eventShapeIsValid(value: Record<string, unknown>, eventType: V4EventType): boolean {
  const isControl = CONTROL_EVENT_TYPES.has(eventType as V4ControlEventType);
  const expectedSchema = isControl
    ? "ai-platform.public-run-stream-control.v4"
    : "ai-platform.public-run-stream-event.v4";
  if (!hasOnlyKeys(value, isControl ? CONTROL_KEYS : APPLICATION_KEYS)) return false;
  if (value.schema !== expectedSchema || value.event_type !== eventType) return false;
  if (!nonEmptyString(value.event_id) || value.event_id.length > 256 || !nonEmptyString(value.run_id) || !SAFE_REF_PATTERN.test(value.run_id)) return false;
  if (isControl) {
    if (value.message_id !== null || value.seq !== null || value.trace_ref !== null) return false;
    if (typeof value.replayable !== "boolean") return false;
  } else if (
    ["message.started", "message.delta", "message.completed", "thinking.started", "thinking.completed", "model.completed", "tool.started", "tool.completed", "tool.failed", "tool.denied", "subagent.started", "subagent.progress", "subagent.completed", "subagent.failed", "subagent.cancelled"].includes(eventType) &&
    (!nonEmptyString(value.message_id) || !SAFE_REF_PATTERN.test(value.message_id))
  ) {
    return false;
  } else if (
    value.message_id !== null && (!nonEmptyString(value.message_id) || !SAFE_REF_PATTERN.test(value.message_id))
  ) {
    return false;
  }
  if (!isControl && (!safeInteger(value.seq, 1) || value.replayable !== true)) return false;
  if (isControl && ((eventType === "stream.open" || eventType === "stream.end") !== value.replayable)) return false;
  if (value.trace_ref !== null && (!nonEmptyString(value.trace_ref) || value.trace_ref.length > 128 || !SAFE_REF_PATTERN.test(value.trace_ref))) return false;
  if (value.causation_event_id !== null && (!nonEmptyString(value.causation_event_id) || !SAFE_REF_PATTERN.test(value.causation_event_id))) return false;
  if (!safeInteger(value.stream_incarnation, 1) || !nonEmptyString(value.emitted_at) || value.emitted_at.length > 64 || Number.isNaN(Date.parse(value.emitted_at))) return false;
  return payloadIsValid(eventType, value.payload);
}

function semanticKey(value: Record<string, unknown>, eventType: V4EventType): string {
  const payload = value.payload as Record<string, unknown>;
  const operationId = typeof payload.operation_id === "string" ? payload.operation_id : "";
  const subagentId = typeof payload.subagent_id === "string" ? payload.subagent_id : "";
  const artifactId = typeof payload.artifact_id === "string" ? payload.artifact_id : "";
  const decisionId = typeof payload.decision_id === "string" ? payload.decision_id : "";
  const terminalId = typeof payload.terminal_event_id === "string" ? payload.terminal_event_id : "";
  const durableSequence = typeof value.seq === "number" ? String(value.seq) : "";
  return [eventType, value.message_id ?? "", operationId || subagentId || artifactId || decisionId || terminalId, durableSequence].join(":");
}

export function adaptPublicRunStreamEventV4(
  frame: V4SseFrame,
  binding: V4AdapterBinding,
): V4PublicEvent | null {
  if (!nonEmptyString(frame.eventHeader) || !nonEmptyString(frame.transportCursor)) return null;
  if (!isRecord(frame.value)) return null;
  const eventType = frame.value.event_type;
  if (typeof eventType !== "string") return null;
  if (!APPLICATION_EVENT_TYPES.has(eventType) && !CONTROL_EVENT_TYPES.has(eventType as V4ControlEventType)) return null;
  if (!eventShapeIsValid(frame.value, eventType as V4EventType)) return null;
  if (frame.eventHeader !== eventType || frame.value.run_id !== binding.runId) return null;
  const incarnation = frame.value.stream_incarnation as number;
  if (binding.streamIncarnation != null && binding.streamIncarnation !== incarnation) return null;
  if (binding.generation != null && frame.generation !== binding.generation) return null;
  return {
    event: frame.value as PublicRunStreamEventV4,
    eventId: frame.value.event_id as string,
    transportCursor: frame.transportCursor,
    runId: frame.value.run_id as string,
    messageId: (frame.value.message_id as string | null) ?? null,
    sequence: (frame.value.seq as number | null) ?? null,
    eventType: eventType as V4EventType,
    streamIncarnation: incarnation,
    generation: frame.generation,
    emittedAt: frame.value.emitted_at as string,
    semanticKey: semanticKey(frame.value, eventType as V4EventType),
  };
}

export interface V4LegacyDispatchEvent {
  streamEvent: StreamEvent;
  messageId: string;
}

/** Convert v4 public events into the existing useAgent handler vocabulary. */
export function projectV4EventToLegacyHandler(event: V4PublicEvent, fallbackMessageId: string): V4LegacyDispatchEvent | null {
  const payload = (event.event as unknown as { payload: Record<string, unknown> }).payload;
  const base = { event_id: event.eventId, run_id: event.runId, sequence: event.sequence, timestamp: event.emittedAt };
  switch (event.eventType) {
    case "stream.open":
      return { streamEvent: { event: "stream_open", data: JSON.stringify(base) }, messageId: fallbackMessageId };
    case "stream.heartbeat":
      return { streamEvent: { event: "heartbeat", data: JSON.stringify(base) }, messageId: fallbackMessageId };
    case "stream.end":
      return { streamEvent: { event: "end", data: JSON.stringify({ ...base, status: "succeeded" }) }, messageId: fallbackMessageId };
    case "stream.gap":
      return null;
    case "message.started":
      return { streamEvent: { event: "message:chunk", data: JSON.stringify({ ...base, content: "", projection_version: "ai-platform.chat-public-projection.v1", projection_kind: "assistant_delta" }) }, messageId: event.messageId || fallbackMessageId };
    case "message.delta":
      return { streamEvent: { event: "message:chunk", data: JSON.stringify({ ...base, content: payload.delta, projection_version: "ai-platform.chat-public-projection.v1", projection_kind: "assistant_delta" }) }, messageId: event.messageId || fallbackMessageId };
    case "message.completed":
      return { streamEvent: { event: "message:chunk", data: JSON.stringify({ ...base, content: payload.content, projection_version: "ai-platform.chat-public-projection.v1", projection_kind: "assistant_final" }) }, messageId: event.messageId || fallbackMessageId };
    case "thinking.started":
    case "thinking.completed":
      return { streamEvent: { event: "thinking", data: JSON.stringify({ ...base, content: "", thinking_id: event.eventId }) }, messageId: event.messageId || fallbackMessageId };
    case "model.completed":
      return { streamEvent: { event: "token:usage", data: JSON.stringify({ ...base, input_tokens: 0, output_tokens: 0, duration: payload.duration_ms, turn_count: payload.turn_count, stop_category: payload.stop_category }) }, messageId: event.messageId || fallbackMessageId };
    case "artifact.created":
    case "artifact.ready":
    case "artifact.failed":
      return { streamEvent: { event: "artifact_card", data: JSON.stringify({ ...base, artifact_id: payload.artifact_id, filename: payload.filename, media_type: payload.media_type, size_bytes: payload.size_bytes, status: payload.status, evidence_ref: payload.evidence_ref ?? null }) }, messageId: fallbackMessageId };
    case "run.succeeded":
      return { streamEvent: { event: "done", data: JSON.stringify({ ...base, status: "succeeded", hydrate_required: true }) }, messageId: fallbackMessageId };
    case "run.cancelled":
      return { streamEvent: { event: "user:cancel", data: JSON.stringify({ ...base, status: "cancelled", hydrate_required: true }) }, messageId: fallbackMessageId };
    case "run.failed":
      return { streamEvent: { event: "error", data: JSON.stringify({ ...base, status: "failed", detail_code: payload.code, detail_kind: "failed", hydrate_required: true }) }, messageId: fallbackMessageId };
    case "run.cancel_requested":
      return { streamEvent: { event: "run_event", data: JSON.stringify({ ...base, event_type: "cancel_requested", projection_version: "ai-platform.chat-public-projection.v1", message: "Cancellation requested" }) }, messageId: event.messageId || fallbackMessageId };
    default:
      return { streamEvent: { event: "run_event", data: JSON.stringify({ ...base, event_type: event.eventType, projection_version: "ai-platform.chat-public-projection.v1" }) }, messageId: event.messageId || fallbackMessageId };
  }
}
