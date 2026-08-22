import {
  PUBLIC_STREAM_EVENT_TYPES,
  type PublicRunStreamEventV4,
} from "../../../generated/publicRunStreamV4";

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
  "model.completed": ["model_id", "input_tokens", "output_tokens"],
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
  "model.completed": ["model_id", "input_tokens", "output_tokens"],
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

function safeInteger(value: unknown, minimum = 0): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= minimum;
}

function payloadIsValid(eventType: string, payload: unknown): payload is Record<string, unknown> {
  if (!isRecord(payload)) return false;
  const allowed = PAYLOAD_KEYS[eventType];
  if (!allowed || !hasOnlyKeys(payload, allowed)) return false;
  for (const key of REQUIRED_PAYLOAD_KEYS[eventType] || []) {
    if (!(key in payload)) return false;
  }
  const stringKeys = new Set([
    "delta", "content", "model_id", "operation_id", "category", "display_name",
    "input_summary", "result_summary", "failure_category", "denial_code",
    "subagent_id", "current_category", "failure_category", "reason_code",
    "artifact_id", "filename", "media_type", "status", "decision_id",
    "decision_code", "source", "terminal_event_id", "projection_version", "code",
    "default_message", "design_id", "recovery", "reason",
  ]);
  const numberKeys = new Set(["input_tokens", "output_tokens", "duration_ms", "size_bytes", "progress_percent", "current_stream_incarnation"]);
  for (const [key, value] of Object.entries(payload)) {
    if (stringKeys.has(key) && !nonEmptyString(value)) return false;
    if (numberKeys.has(key) && !safeInteger(value, 0)) return false;
    if (key === "hydrate_required" && value !== true) return false;
    if (key.endsWith("_refs") && (!Array.isArray(value) || value.some((entry) => !nonEmptyString(entry)))) return false;
    if (key === "detail" && value !== null && !nonEmptyString(value)) return false;
    if (key === "evidence_ref" && value !== null && !nonEmptyString(value)) return false;
    if (key.endsWith("_event_id") && !nonEmptyString(value)) return false;
    if (key.endsWith("_stream_incarnation") && value !== null && !safeInteger(value, 1)) return false;
    if (key === "requested_event_id" && value !== null && !nonEmptyString(value)) return false;
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
  if (!nonEmptyString(value.event_id) || !nonEmptyString(value.run_id)) return false;
  if (isControl) {
    if (value.message_id !== null || value.seq !== null || value.trace_ref !== null) return false;
    if (typeof value.replayable !== "boolean") return false;
  } else if (
    !nonEmptyString(value.message_id) ||
    !safeInteger(value.seq) ||
    value.replayable !== true ||
    (value.trace_ref !== null && !nonEmptyString(value.trace_ref))
  ) {
    return false;
  }
  if (value.causation_event_id !== null && !nonEmptyString(value.causation_event_id)) return false;
  if (!safeInteger(value.stream_incarnation, 1) || !nonEmptyString(value.emitted_at)) return false;
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

export function isV4TerminalEvent(event: V4PublicEvent): boolean {
  return event.eventType === "run.succeeded" || event.eventType === "run.cancelled" || event.eventType === "run.failed";
}
