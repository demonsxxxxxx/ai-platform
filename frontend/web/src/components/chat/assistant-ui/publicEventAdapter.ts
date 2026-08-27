import {
  PUBLIC_STREAM_EVENT_TYPES,
  type PublicRunStreamEventV4,
} from "../../../generated/publicRunStreamV4";
import {
  isPublicAgentProgressPayload,
  type StreamEvent,
} from "../../../hooks/useAgent/types";

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
  readonly causationEventId: string | null;
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
  "thinking.started": ["thinking_id", "public_summary"],
  "thinking.delta": ["thinking_id", "delta"],
  "thinking.completed": ["thinking_id", "public_summary"],
  "agent.progress": ["schema_version", "step_id", "phase", "lifecycle", "message"],
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
  "thinking.delta": ["thinking_id", "delta"],
  "agent.progress": ["schema_version", "step_id", "phase", "lifecycle", "message"],
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

function boundedCodePointString(
  value: unknown,
  maximum: number,
  requireNonEmpty = false,
): value is string {
  if (typeof value !== "string") return false;
  let length = 0;
  for (const _character of value) {
    length += 1;
    if (length > maximum) return false;
  }
  return !requireNonEmpty || length > 0;
}

function safeInteger(value: unknown, minimum = 0, maximum = Number.MAX_SAFE_INTEGER): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= minimum && value <= maximum;
}

const RUN_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
const REDIS_ID_PATTERN = /^(0|[1-9][0-9]*)-(0|[1-9][0-9]*)$/;
const SAFE_REF_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,255}$/;
const SAFE_FILENAME_PATTERN = /^[^/\\]+$/;
const TOOL_CATEGORIES = new Set(["skill", "mcp", "read", "write", "edit", "search", "execute"]);
const PAYLOAD_ENUMS: Record<string, ReadonlySet<string>> = {
  category: TOOL_CATEGORIES,
  current_category: TOOL_CATEGORIES,
  stop_category: new Set(["completed", "max_turns", "cancelled", "failed", "unknown"]),
  failure_category: new Set(["invalid_input", "not_found", "permission_denied", "timeout", "unavailable", "execution_failed", "subagent_failed", "artifact_failed"]),
  denial_code: new Set(["capability_not_authorized", "policy_denied"]),
  reason_code: new Set(["user_cancelled", "run_cancelled", "policy_cancelled", "timeout"]),
  source: new Set(["user", "system"]),
  status: new Set(["created", "ready", "failed"]),
  reason: new Set(["retained_history_unavailable", "stream_missing", "stream_continuity_unproven", "stream_incarnation_mismatch"]),
  recovery: new Set(["reload_durable_state"]),
  decision_code: new Set(["allowed", "capability_not_authorized", "policy_denied"]),
  design_id: new Set(["ai-platform.redis-streams-sse-event-channel.v4"]),
  projection_version: new Set(["ai-platform.chat-public-projection.v1"]),
  schema_version: new Set(["ai-platform.public-agent-progress.v1"]),
  phase: new Set(["attachment_materialization", "skill_staging", "sandbox_preparation", "sandbox_submission", "model_wait", "artifact_validation", "artifact_recovery"]),
  lifecycle: new Set(["started", "progress", "completed", "failed"]),
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
  "thinking.started.public_summary": new Set(["Analyzing the request"]),
  "thinking.completed.public_summary": new Set(["Analysis step completed"]),
};
const PAYLOAD_STRING_MAX: Record<string, number> = {
  delta: 8192, content: 262144, display_name: 128, public_summary: 512,
  input_summary: 512, result_summary: 2048, message: 128, media_type: 128,
  default_message: 1024, detail: 2048, code: 128,
};
const NON_EMPTY_PAYLOAD_STRINGS = new Set(["delta", "display_name", "public_summary", "message", "media_type", "code", "default_message"]);
const PAYLOAD_NUMBER_MAX: Record<string, number> = {
  duration_ms: 86400000, turn_count: 10000, progress_percent: 100, size_bytes: 1099511627776,
};

function parseTransportCursor(value: unknown, runId: string): { incarnation: number; redisId: string } | null {
  if (typeof value !== "string") return null;
  const prefix = `${runId}:`;
  if (!value.startsWith(prefix)) return null;
  const remainder = value.slice(prefix.length);
  const separator = remainder.indexOf(":");
  if (separator <= 0) return null;
  const incarnationText = remainder.slice(0, separator);
  if (!/^[1-9][0-9]*$/.test(incarnationText)) return null;
  const incarnation = Number(incarnationText);
  const redisId = remainder.slice(separator + 1);
  if (!Number.isSafeInteger(incarnation) || !REDIS_ID_PATTERN.test(redisId)) return null;
  return { incarnation, redisId };
}

export function comparePublicRunStreamCursors(
  left: string,
  right: string,
): number | null {
  const parse = (value: string) => {
    const redisSeparator = value.lastIndexOf(":");
    const incarnationSeparator = value.lastIndexOf(":", redisSeparator - 1);
    if (incarnationSeparator <= 0) return null;
    const runId = value.slice(0, incarnationSeparator);
    if (!RUN_ID_PATTERN.test(runId)) return null;
    const parsed = parseTransportCursor(value, runId);
    if (!parsed) return null;
    const [redisMs, redisSequence] = parsed.redisId.split("-").map(BigInt);
    return { runId, incarnation: parsed.incarnation, redisMs, redisSequence };
  };
  const leftParts = parse(left);
  const rightParts = parse(right);
  if (
    !leftParts ||
    !rightParts ||
    leftParts.runId !== rightParts.runId ||
    leftParts.incarnation !== rightParts.incarnation
  ) {
    return null;
  }
  if (leftParts.redisMs !== rightParts.redisMs) {
    return leftParts.redisMs < rightParts.redisMs ? -1 : 1;
  }
  if (leftParts.redisSequence === rightParts.redisSequence) return 0;
  return leftParts.redisSequence < rightParts.redisSequence ? -1 : 1;
}

function isValidTransportCursor(value: unknown, runId: string, incarnation: number): value is string {
  return parseTransportCursor(value, runId)?.incarnation === incarnation;
}

function isSafeFilename(value: unknown): value is string {
  if (!boundedCodePointString(value, 255, true) || !SAFE_FILENAME_PATTERN.test(value)) return false;
  return [...value].every((character) => {
    const code = character.charCodeAt(0);
    return code > 0x1f && code !== 0x7f;
  });
}
function isPayloadRefKey(key: string): boolean {
  return ["thinking_id", "operation_id", "artifact_id", "decision_id", "subagent_id", "terminal_event_id", "step_id"].includes(key);
}

function isNullableSafeRef(value: unknown): boolean {
  return value === null || (typeof value === "string" && SAFE_REF_PATTERN.test(value));
}

function isRfc3339DateTime(value: unknown): value is string {
  if (typeof value !== "string" || value.length === 0 || value.length > 64) return false;
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})$/.exec(value);
  if (!match) return false;
  const [, year, month, day, hour, minute, second, , zone] = match;
  const y = Number(year), m = Number(month), d = Number(day);
  if (m < 1 || m > 12 || d < 1 || d > new Date(Date.UTC(y, m, 0)).getUTCDate()) return false;
  if (Number(hour) > 23 || Number(minute) > 59 || Number(second) > 59) return false;
  if (zone !== "Z" && (Number(zone.slice(1, 3)) > 23 || Number(zone.slice(4, 6)) > 59)) return false;
  return !Number.isNaN(Date.parse(value));
}

function payloadIsValid(eventType: string, payload: unknown, _runId: string, incarnation: number): payload is Record<string, unknown> {
  if (!isRecord(payload) || Object.keys(payload).length > 64) return false;
  const allowed = PAYLOAD_KEYS[eventType];
  if (!allowed || !hasOnlyKeys(payload, allowed)) return false;
  for (const key of REQUIRED_PAYLOAD_KEYS[eventType] || []) {
    if (!Object.hasOwn(payload, key)) return false;
  }
  if (eventType === "agent.progress" && !isPublicAgentProgressPayload(payload)) {
    return false;
  }
  for (const [key, value] of Object.entries(payload)) {
    const enumValues = EVENT_PAYLOAD_ENUMS[`${eventType}.${key}`] || PAYLOAD_ENUMS[key];
    if (enumValues && (typeof value !== "string" || !enumValues.has(value))) return false;
    if (key.endsWith("_refs")) {
      if (!Array.isArray(value) || value.length > 32 || new Set(value).size !== value.length || value.some((entry) => typeof entry !== "string" || !SAFE_REF_PATTERN.test(entry))) return false;
    }
    if (isPayloadRefKey(key) && (!nonEmptyString(value) || !SAFE_REF_PATTERN.test(value))) return false;
    if (key === "evidence_ref" && !isNullableSafeRef(value)) return false;
    if (key === "detail" && value === null) continue;
    const stringMax = PAYLOAD_STRING_MAX[key];
    if (
      stringMax !== undefined &&
      !boundedCodePointString(value, stringMax, NON_EMPTY_PAYLOAD_STRINGS.has(key))
    ) return false;
    const numberMax = PAYLOAD_NUMBER_MAX[key];
    if (numberMax !== undefined && !safeInteger(value, 0, numberMax)) return false;
    if (key === "hydrate_required" && value !== true) return false;
    if (["requested_event_id", "earliest_available_event_id", "latest_available_event_id"].includes(key)) {
      if (value !== null && typeof value !== "string") return false;
    }
    if (["requested_stream_incarnation"].includes(key) && value !== null && !safeInteger(value, 1)) return false;
    if (["current_stream_incarnation"].includes(key) && !safeInteger(value, 1)) return false;
    if (key === "filename" && !isSafeFilename(value)) return false;
  }
  if (eventType === "stream.gap") {
    const requestedIncarnation = payload.requested_stream_incarnation;
    const currentIncarnation = payload.current_stream_incarnation;
    if (
      (requestedIncarnation !== null && !safeInteger(requestedIncarnation, 1)) ||
      currentIncarnation !== incarnation
    ) {
      return false;
    }
    for (const key of [
      "requested_event_id",
      "earliest_available_event_id",
      "latest_available_event_id",
    ] as const) {
      const redisId = payload[key];
      if (redisId !== null && (
        typeof redisId !== "string" || !REDIS_ID_PATTERN.test(redisId)
      )) {
        return false;
      }
    }
  }
  return true;
}

function eventShapeIsValid(value: Record<string, unknown>, eventType: V4EventType): boolean {
  const isControl = CONTROL_EVENT_TYPES.has(eventType as V4ControlEventType);
  const expectedSchema = isControl
    ? "ai-platform.public-run-stream-control.v4"
    : "ai-platform.public-run-stream-event.v4";
  const keys = isControl ? CONTROL_KEYS : APPLICATION_KEYS;
  if (Object.keys(value).length !== keys.length || !hasOnlyKeys(value, keys)) return false;
  if (value.schema !== expectedSchema || value.event_type !== eventType) return false;
  if (!nonEmptyString(value.event_id) || value.event_id.length > 256 || !SAFE_REF_PATTERN.test(value.event_id) || !nonEmptyString(value.run_id) || !RUN_ID_PATTERN.test(value.run_id)) return false;
  if (isControl) {
    if (value.message_id !== null || value.seq !== null || value.trace_ref !== null || typeof value.replayable !== "boolean") return false;
  } else if (
    ["message.started", "message.delta", "message.completed", "thinking.started", "thinking.delta", "thinking.completed", "model.completed", "tool.started", "tool.completed", "tool.failed", "tool.denied", "subagent.started", "subagent.progress", "subagent.completed", "subagent.failed", "subagent.cancelled"].includes(eventType) &&
    (!nonEmptyString(value.message_id) || !SAFE_REF_PATTERN.test(value.message_id))
  ) {
    return false;
  } else if (value.message_id !== null && (!nonEmptyString(value.message_id) || !SAFE_REF_PATTERN.test(value.message_id))) {
    return false;
  }
  if (!isControl && (!safeInteger(value.seq, 1) || value.replayable !== true)) return false;
  if (isControl && ((eventType === "stream.open" || eventType === "stream.end") !== value.replayable)) return false;
  if (value.trace_ref !== null && (!nonEmptyString(value.trace_ref) || value.trace_ref.length > 128 || !SAFE_REF_PATTERN.test(value.trace_ref))) return false;
  if (value.causation_event_id !== null && (!nonEmptyString(value.causation_event_id) || !SAFE_REF_PATTERN.test(value.causation_event_id))) return false;
  if (!safeInteger(value.stream_incarnation, 1) || !isRfc3339DateTime(value.emitted_at)) return false;
  return payloadIsValid(eventType, value.payload, value.run_id as string, value.stream_incarnation as number);
}

function semanticKey(value: Record<string, unknown>, _eventType: V4EventType): string {
  return value.event_id as string;
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
  if (!isValidTransportCursor(frame.transportCursor, binding.runId, incarnation)) return null;
  const payload = frame.value.payload as Record<string, unknown>;
  const acceptedCrossIncarnationGap =
    eventType === "stream.gap" &&
    binding.streamIncarnation != null &&
    payload.requested_stream_incarnation === binding.streamIncarnation &&
    payload.current_stream_incarnation === incarnation;
  if (
    binding.streamIncarnation != null &&
    binding.streamIncarnation !== incarnation &&
    !acceptedCrossIncarnationGap
  ) return null;
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
    causationEventId: (frame.value.causation_event_id as string | null) ?? null,
  };
}

export interface V4LegacyDispatchEvent {
  streamEvent: StreamEvent;
  messageId: string;
}

/** Convert v4 public events into the existing useAgent handler vocabulary. */
export function projectV4EventToLegacyHandler(event: V4PublicEvent, fallbackMessageId: string): V4LegacyDispatchEvent | null {
  const payload = (event.event as unknown as { payload: Record<string, unknown> }).payload;
  const base = {
    event_id: event.eventId,
    message_id: event.messageId,
    run_id: event.runId,
    sequence: event.sequence,
    timestamp: event.emittedAt,
    trace_ref: (event.event as unknown as { trace_ref: string | null }).trace_ref,
    causation_event_id: event.causationEventId,
  };
  const messageTarget = fallbackMessageId;
  const activity = (
    phase: string,
    message: string,
    severity: "info" | "warning" | "error" = "info",
    activityPayload?: Record<string, unknown>,
  ) => ({
    event: "run_event" as const,
    data: JSON.stringify({
      ...base,
      event_type: "public_activity",
      projection_version: "ai-platform.chat-public-projection.v1",
      stage: phase,
      status: phase,
      severity,
      message,
      ...(activityPayload ? { payload: activityPayload } : {}),
    }),
  });
  const publicTool = (status: "started" | "completed" | "failed" | "denied") => ({
    event: "run_event" as const,
    data: JSON.stringify({
      ...base,
      event_type: "public_tool_activity",
      operation_id: payload.operation_id,
      category: payload.category,
      display_name: payload.display_name,
      status,
      duration_ms: payload.duration_ms,
      result_summary: payload.result_summary,
      failure_category: payload.failure_category,
      denial_code: payload.denial_code,
      input_summary: payload.input_summary,
      evidence_refs: payload.evidence_refs,
      artifact_refs: payload.artifact_refs,
    }),
  });
  const publicSubagent = (status: "started" | "progress" | "completed" | "failed" | "cancelled") => ({
    event: "run_event" as const,
    data: JSON.stringify({
      ...base,
      event_type: "public_subagent_activity",
      subagent_id: payload.subagent_id,
      display_name: payload.display_name,
      status,
      duration_ms: payload.duration_ms,
      progress_percent: payload.progress_percent,
      current_category: payload.current_category,
      // causation_event_id remains an event identity. The reducer resolves a
      // parent subagent only from an already accepted parent event.
      causation_event_id: event.causationEventId,
    }),
  });
  switch (event.eventType) {
    case "stream.open":
      return { streamEvent: { event: "stream_open", data: JSON.stringify(base) }, messageId: fallbackMessageId };
    case "stream.heartbeat":
      return { streamEvent: { event: "heartbeat", data: JSON.stringify(base) }, messageId: fallbackMessageId };
    case "stream.end":
      return { streamEvent: { event: "end", data: JSON.stringify({ ...base, payload: { terminal_event_id: payload.terminal_event_id } }) }, messageId: fallbackMessageId };
    case "stream.gap":
      return null;
    case "message.started":
      return { streamEvent: { event: "run_event", data: JSON.stringify({ ...base, event_type: "public_activity", projection_version: "ai-platform.chat-public-projection.v1", stage: "message_started", status: "running", message: "Assistant response started" }) }, messageId: messageTarget };
    case "message.delta":
      return { streamEvent: { event: "message:chunk", data: JSON.stringify({ ...base, content: payload.delta, projection_version: "ai-platform.chat-public-projection.v1", projection_kind: "assistant_delta" }) }, messageId: messageTarget };
    case "message.completed":
      return { streamEvent: { event: "message:chunk", data: JSON.stringify({ ...base, content: payload.content, projection_version: "ai-platform.chat-public-projection.v1", projection_kind: "assistant_final" }) }, messageId: messageTarget };
    case "thinking.started":
      return { streamEvent: activity("thinking_started", typeof payload.public_summary === "string" ? payload.public_summary : "", "info", payload), messageId: messageTarget };
    case "thinking.delta":
      return { streamEvent: activity("thinking_delta", payload.delta as string, "info", payload), messageId: messageTarget };
    case "thinking.completed":
      return { streamEvent: activity("thinking_completed", typeof payload.public_summary === "string" ? payload.public_summary : "", "info", payload), messageId: messageTarget };
    case "agent.progress":
      return { streamEvent: { event: "run_event", data: JSON.stringify({
        ...base,
        event_type: "agent_public_progress",
        projection_version: "ai-platform.chat-public-projection.v1",
        stage: payload.phase,
        status: payload.lifecycle,
        message: payload.message,
        payload,
      }) }, messageId: fallbackMessageId };
    case "model.completed":
      return { streamEvent: activity("model_completed", "Model response complete", "info"), messageId: messageTarget };
    case "tool.started":
      return { streamEvent: publicTool("started"), messageId: messageTarget };
    case "tool.completed":
      return { streamEvent: publicTool("completed"), messageId: messageTarget };
    case "tool.failed":
      return { streamEvent: publicTool("failed"), messageId: messageTarget };
    case "tool.denied":
      return { streamEvent: publicTool("denied"), messageId: messageTarget };
    case "subagent.started":
      return { streamEvent: publicSubagent("started"), messageId: messageTarget };
    case "subagent.progress":
      return { streamEvent: publicSubagent("progress"), messageId: messageTarget };
    case "subagent.completed":
      return { streamEvent: publicSubagent("completed"), messageId: messageTarget };
    case "subagent.failed":
      return { streamEvent: publicSubagent("failed"), messageId: messageTarget };
    case "subagent.cancelled":
      return { streamEvent: publicSubagent("cancelled"), messageId: messageTarget };
    case "artifact.created":
    case "artifact.ready":
    case "artifact.failed":
      return { streamEvent: { event: "artifact_card", data: JSON.stringify({
        ...base,
        artifact_id: payload.artifact_id,
        artifact_type: payload.media_type || "artifact",
        label: payload.filename || "Artifact unavailable",
        content_type: payload.media_type || "application/octet-stream",
        size_bytes: payload.size_bytes ?? 0,
        status: payload.status,
      }) }, messageId: fallbackMessageId };
    case "policy.checking":
      return { streamEvent: activity("policy_checking", payload.display_name as string), messageId: messageTarget };
    case "policy.allowed":
      return { streamEvent: activity("policy_allowed", payload.display_name as string), messageId: messageTarget };
    case "policy.denied":
      return { streamEvent: activity("policy_denied", payload.display_name as string, "warning"), messageId: messageTarget };
    case "run.succeeded":
      return { streamEvent: { event: "done", data: JSON.stringify({ ...base, status: "succeeded", hydrate_required: true }) }, messageId: fallbackMessageId };
    case "run.cancelled":
      return { streamEvent: { event: "final_detail", data: JSON.stringify({ ...base, projection_version: "ai-platform.chat-public-projection.v1", detail_code: "run_cancelled", detail_kind: "cancelled" }) }, messageId: fallbackMessageId };
    case "run.failed":
      return { streamEvent: { event: "final_detail", data: JSON.stringify({ ...base, projection_version: "ai-platform.chat-public-projection.v1", detail_code: payload.code, detail_kind: "failed" }) }, messageId: fallbackMessageId };
    case "run.cancel_requested":
      return { streamEvent: activity("cancel_requested", "Cancellation requested", "warning"), messageId: messageTarget };
    default:
      return null;
  }
}
