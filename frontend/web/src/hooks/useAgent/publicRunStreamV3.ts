import {
  PUBLIC_RUN_STREAM_SCHEMA,
  STREAM_DESIGN_ID,
  type PublicRunStreamEventV3,
} from "../../generated/publicRunStreamV3";
import {
  CHAT_PUBLIC_PROJECTION_VERSION,
  type EventData,
  type EventType,
} from "./types";

const EVENT_FIELDS = new Set([
  "schema",
  "event_id",
  "run_id",
  "stream_incarnation",
  "emitted_at",
  "event_type",
  "payload",
]);

const SEMANTIC_PROGRESS_EVENTS = new Set([
  "execution_progress",
  "execution_step",
  "execution_step_completed",
  "execution_step_failed",
]);
const RUN_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
const REDIS_ID_PATTERN = /^(0|[1-9][0-9]*)-(0|[1-9][0-9]*)$/;
const RFC3339_PATTERN =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
const MAX_EVENT_ID_LENGTH = 256;
const MAX_EMITTED_AT_LENGTH = 64;
const MAX_DELTA_LENGTH = 8192;
const MAX_SEMANTIC_DATA_PROPERTIES = 64;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(
  value: Record<string, unknown>,
  expected: ReadonlySet<string>,
): boolean {
  const keys = Object.keys(value);
  return keys.length === expected.size && keys.every((key) => expected.has(key));
}

function hasBoundedString(
  value: Record<string, unknown>,
  key: string,
  maxLength: number,
): boolean {
  return (
    typeof value[key] === "string" &&
    value[key].length > 0 &&
    value[key].length <= maxLength
  );
}

function hasBoundedSemanticData(value: unknown): value is Record<string, unknown> {
  return (
    isRecord(value) &&
    Object.keys(value).length <= MAX_SEMANTIC_DATA_PROPERTIES
  );
}

function isRfc3339DateTime(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value.length <= MAX_EMITTED_AT_LENGTH &&
    RFC3339_PATTERN.test(value) &&
    !Number.isNaN(Date.parse(value))
  );
}

export function comparePublicRunStreamCursors(
  left: string,
  right: string,
): number | null {
  const leftParts = parsePublicRunStreamCursor(left);
  const rightParts = parsePublicRunStreamCursor(right);
  if (!leftParts || !rightParts) return null;
  if (
    leftParts.runId !== rightParts.runId ||
    leftParts.streamIncarnation !== rightParts.streamIncarnation
  ) {
    return null;
  }
  if (leftParts.redisMs !== rightParts.redisMs) {
    return leftParts.redisMs < rightParts.redisMs ? -1 : 1;
  }
  if (leftParts.redisSequence === rightParts.redisSequence) return 0;
  return leftParts.redisSequence < rightParts.redisSequence ? -1 : 1;
}

function parsePublicRunStreamCursor(value: string): {
  runId: string;
  streamIncarnation: bigint;
  redisMs: bigint;
  redisSequence: bigint;
} | null {
  const redisSeparator = value.lastIndexOf(":");
  if (redisSeparator <= 0) return null;
  const incarnationSeparator = value.lastIndexOf(":", redisSeparator - 1);
  if (incarnationSeparator <= 0) return null;
  const runId = value.slice(0, incarnationSeparator);
  const incarnation = value.slice(incarnationSeparator + 1, redisSeparator);
  const redisId = value.slice(redisSeparator + 1);
  if (
    !RUN_ID_PATTERN.test(runId) ||
    !/^\d+$/.test(incarnation) ||
    (incarnation.length > 1 && incarnation.startsWith("0")) ||
    !REDIS_ID_PATTERN.test(redisId)
  ) {
    return null;
  }
  const redisParts = redisId.split("-");
  try {
    return {
      runId,
      streamIncarnation: BigInt(incarnation),
      redisMs: BigInt(redisParts[0]),
      redisSequence: BigInt(redisParts[1]),
    };
  } catch {
    return null;
  }
}

export interface AdaptedPublicRunStreamEvent {
  event: EventType;
  data: EventData;
  emittedAt: string;
  streamIncarnation: number;
}

export function adaptPublicRunStreamEventV3({
  eventHeader,
  transportCursor,
  value,
  targetRunId,
  targetStreamIncarnation,
}: {
  eventHeader: string;
  transportCursor: string;
  value: unknown;
  targetRunId: string;
  targetStreamIncarnation?: number | null;
}): AdaptedPublicRunStreamEvent | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, EVENT_FIELDS) ||
    value.schema !== PUBLIC_RUN_STREAM_SCHEMA ||
    value.run_id !== targetRunId ||
    value.event_type !== eventHeader ||
    !hasBoundedString(value, "event_id", MAX_EVENT_ID_LENGTH) ||
    typeof value.run_id !== "string" ||
    !RUN_ID_PATTERN.test(value.run_id) ||
    !isRfc3339DateTime(value.emitted_at) ||
    !Number.isSafeInteger(value.stream_incarnation) ||
    Number(value.stream_incarnation) < 1 ||
    !transportCursor.startsWith(
      `${targetRunId}:${String(value.stream_incarnation)}:`,
    ) ||
    !REDIS_ID_PATTERN.test(
      transportCursor.slice(
        `${targetRunId}:${String(value.stream_incarnation)}:`.length,
      ),
    ) ||
    (targetStreamIncarnation != null &&
      value.stream_incarnation !== targetStreamIncarnation) ||
    !isRecord(value.payload)
  ) {
    return null;
  }
  const parsed = value as unknown as PublicRunStreamEventV3;
  const base = {
    event_id: parsed.event_id,
    run_id: parsed.run_id,
  };
  switch (parsed.event_type) {
    case "stream_open":
      if (
        !hasExactKeys(parsed.payload, new Set(["design_id"])) ||
        parsed.payload.design_id !== STREAM_DESIGN_ID
      ) {
        return null;
      }
      return {
        event: "stream_open",
        data: base,
        emittedAt: parsed.emitted_at,
        streamIncarnation: parsed.stream_incarnation,
      };
    case "assistant_text_delta":
      if (
        !hasExactKeys(parsed.payload, new Set(["delta"])) ||
        typeof parsed.payload.delta !== "string" ||
        !parsed.payload.delta ||
        parsed.payload.delta.length > MAX_DELTA_LENGTH
      ) {
        return null;
      }
      return {
        event: "message:chunk",
        data: {
          ...base,
          // This compatibility event feeds the existing chat reducer, whose
          // versioned text projection is distinct from the v3 transport envelope.
          projection_version: CHAT_PUBLIC_PROJECTION_VERSION,
          projection_kind: "assistant_delta",
          content: parsed.payload.delta,
        },
        emittedAt: parsed.emitted_at,
        streamIncarnation: parsed.stream_incarnation,
      };
    case "semantic_stage":
      if (
        !hasExactKeys(parsed.payload, new Set(["event", "data"])) ||
        parsed.payload.event !== "run_event" ||
        !hasBoundedSemanticData(parsed.payload.data)
      ) {
        return null;
      }
      return {
        event: "run_event",
        data: { ...parsed.payload.data, ...base },
        emittedAt: parsed.emitted_at,
        streamIncarnation: parsed.stream_incarnation,
      };
    case "semantic_progress":
      if (
        !hasExactKeys(parsed.payload, new Set(["event", "data"])) ||
        !SEMANTIC_PROGRESS_EVENTS.has(parsed.payload.event) ||
        !hasBoundedSemanticData(parsed.payload.data)
      ) {
        return null;
      }
      return {
        event: parsed.payload.event as EventType,
        data: { ...parsed.payload.data, ...base },
        emittedAt: parsed.emitted_at,
        streamIncarnation: parsed.stream_incarnation,
      };
    case "terminal":
      if (
        !hasExactKeys(
          parsed.payload,
          new Set(["event_id", "hydrate_required", "status"]),
        ) ||
        parsed.payload.event_id !== parsed.event_id ||
        parsed.payload.event_id.length > MAX_EVENT_ID_LENGTH ||
        parsed.payload.hydrate_required !== true ||
        !["succeeded", "failed", "cancelled"].includes(parsed.payload.status)
      ) {
        return null;
      }
      return {
        event: "done",
        data: { ...base, status: parsed.payload.status },
        emittedAt: parsed.emitted_at,
        streamIncarnation: parsed.stream_incarnation,
      };
    case "end":
      if (
        !hasExactKeys(parsed.payload, new Set(["terminal_event_id"])) ||
        typeof parsed.payload.terminal_event_id !== "string" ||
        !parsed.payload.terminal_event_id ||
        parsed.payload.terminal_event_id.length > MAX_EVENT_ID_LENGTH
      ) {
        return null;
      }
      return {
        event: "end",
        data: {
          ...base,
          payload: { terminal_event_id: parsed.payload.terminal_event_id },
        },
        emittedAt: parsed.emitted_at,
        streamIncarnation: parsed.stream_incarnation,
      };
  }
  return null;
}
