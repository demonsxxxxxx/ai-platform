import {
  PUBLIC_APPLICATION_ENVELOPE_FIELDS,
  PUBLIC_APPLICATION_EVENT_TYPES,
  PUBLIC_CONTROL_ENVELOPE_FIELDS,
  PUBLIC_CONTROL_EVENT_TYPES,
  PUBLIC_MESSAGE_CORRELATED_EVENT_TYPES,
  PUBLIC_PAYLOAD_ENUMS,
  PUBLIC_PAYLOAD_FIELDS,
  PUBLIC_PAYLOAD_INTEGER_BOUNDS,
  PUBLIC_PAYLOAD_NULLABLE_REF_FIELDS,
  PUBLIC_PAYLOAD_REF_ARRAY_FIELDS,
  PUBLIC_PAYLOAD_REF_FIELDS,
  PUBLIC_PAYLOAD_STRING_BOUNDS,
  PUBLIC_REQUIRED_PAYLOAD_FIELDS,
  type PublicRunStreamEventV4,
} from "../../../generated/publicRunStreamV4";
import { isPublicAgentProgressPayload } from "../../../hooks/useAgent/types";

export type V4ApplicationEventType = (typeof PUBLIC_APPLICATION_EVENT_TYPES)[number];
export type V4ControlEventType = (typeof PUBLIC_CONTROL_EVENT_TYPES)[number];
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

const APPLICATION_EVENT_TYPES = new Set<string>(PUBLIC_APPLICATION_EVENT_TYPES);
const MESSAGE_CORRELATED_EVENT_TYPES = new Set<V4ApplicationEventType>(
  PUBLIC_MESSAGE_CORRELATED_EVENT_TYPES,
);
const CONTROL_EVENT_TYPES = new Set<V4ControlEventType>(PUBLIC_CONTROL_EVENT_TYPES);

export function isV4MessageCorrelatedEventType(
  eventType: V4EventType,
): eventType is V4ApplicationEventType {
  return MESSAGE_CORRELATED_EVENT_TYPES.has(
    eventType as V4ApplicationEventType,
  );
}

const APPLICATION_KEYS = PUBLIC_APPLICATION_ENVELOPE_FIELDS;
const CONTROL_KEYS = PUBLIC_CONTROL_ENVELOPE_FIELDS;

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
  const characters = value[Symbol.iterator]();
  while (!characters.next().done) {
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
  return PUBLIC_PAYLOAD_REF_FIELDS.includes(key as (typeof PUBLIC_PAYLOAD_REF_FIELDS)[number]);
}

function isNullablePayloadRefKey(key: string): boolean {
  return PUBLIC_PAYLOAD_NULLABLE_REF_FIELDS.includes(key as (typeof PUBLIC_PAYLOAD_NULLABLE_REF_FIELDS)[number]);
}

function isPayloadRefArrayKey(key: string): boolean {
  return PUBLIC_PAYLOAD_REF_ARRAY_FIELDS.includes(key as (typeof PUBLIC_PAYLOAD_REF_ARRAY_FIELDS)[number]);
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
  const allowed = PUBLIC_PAYLOAD_FIELDS[eventType as keyof typeof PUBLIC_PAYLOAD_FIELDS];
  if (!allowed || !hasOnlyKeys(payload, allowed)) return false;
  for (const key of PUBLIC_REQUIRED_PAYLOAD_FIELDS[eventType as keyof typeof PUBLIC_REQUIRED_PAYLOAD_FIELDS] || []) {
    if (!Object.hasOwn(payload, key)) return false;
  }
  if (eventType === "agent.progress" && !isPublicAgentProgressPayload(payload)) {
    return false;
  }
  for (const [key, value] of Object.entries(payload)) {
    const enumValues = (PUBLIC_PAYLOAD_ENUMS as Record<string, readonly unknown[]>)[`${eventType}.${key}`];
    if (enumValues && !enumValues.includes(value)) return false;
    if (isPayloadRefArrayKey(key)) {
      if (!Array.isArray(value) || value.length > 32 || new Set(value).size !== value.length || value.some((entry) => typeof entry !== "string" || !SAFE_REF_PATTERN.test(entry))) return false;
    }
    if (isPayloadRefKey(key) && (!nonEmptyString(value) || !SAFE_REF_PATTERN.test(value))) return false;
    if (isNullablePayloadRefKey(key) && !isNullableSafeRef(value)) return false;
    if (key === "detail" && value === null) continue;
    const stringBounds = (PUBLIC_PAYLOAD_STRING_BOUNDS as Record<string, readonly [number, number]>)[`${eventType}.${key}`];
    if (
      stringBounds !== undefined &&
      !boundedCodePointString(value, stringBounds[1], stringBounds[0] > 0)
    ) return false;
    const numberBounds = (PUBLIC_PAYLOAD_INTEGER_BOUNDS as Record<string, readonly [number, number | null]>)[`${eventType}.${key}`];
    if (numberBounds !== undefined && value !== null && !safeInteger(value, numberBounds[0], numberBounds[1] ?? Number.MAX_SAFE_INTEGER)) return false;
    if (key === "hydrate_required" && value !== true) return false;
    if (
      eventType === "message.completed" &&
      (key === "delta_count" || key === "text_length") &&
      !safeInteger(value, 1, Number.MAX_SAFE_INTEGER)
    ) return false;
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
    isV4MessageCorrelatedEventType(eventType) &&
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
