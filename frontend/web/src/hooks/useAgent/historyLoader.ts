/**
 * History event loader for useAgent hook
 * Reconstructs messages from stored events.
 *
 * Message transformation logic is unified in processMessageEvent (messageParts.ts).
 * This file handles: event iteration, message reconstruction, and
 * user:message / user:cancel which are history-specific.
 */

import type { Message, MessagePart } from "../../types";
import { uuid } from "../../utils/uuid";
import i18n from "../../i18n";
import type {
  EventData,
  SubagentStackItem,
  HistoryEvent,
  HistoryEventData,
} from "./types";
import {
  convertAttachments,
  normalizeMessageTextLogicalIds,
  processMessageEvent,
} from "./eventProcessor";
import { clearAllLoadingStates } from "./messageParts";
import { parseDate } from "../../utils/datetime";
import { getPublicTerminalPresentationDefinition } from "./publicTerminalPresentation";
import { CHAT_PUBLIC_PROJECTION_VERSION } from "./types";
import {
  assistantTextPartRole,
  composeAssistantPreviewText,
  hasMarkedAssistantTextParts,
  isAssistantTextPartEventType,
  isValidAssistantTextPartPayload,
} from "../../types/assistantTextParts";

function resolveUserMessageId(
  event: HistoryEvent,
  eventData: HistoryEventData,
): string {
  if (typeof eventData.message_id === "string" && eventData.message_id.trim()) {
    return eventData.message_id;
  }
  if (typeof event.run_id === "string" && event.run_id.trim()) {
    return `${event.run_id}:user`;
  }
  return uuid();
}

interface ProcessHistoryOptions {
  activeSubagentStack: SubagentStackItem[];
  activeProtocolMessageOwner?: {
    messageId: string;
    streamIncarnation: number;
  } | null;
}

export class InvalidAssistantTextPartHistoryError extends Error {
  constructor() {
    super("Assistant text-part history projection is invalid");
    this.name = "InvalidAssistantTextPartHistoryError";
  }
}

const PROTOCOL_MESSAGE_EVENT_TYPES = new Set([
  "message.started",
  "message.delta",
  "message.completed",
  "message.part.delta",
  "message.part.classified",
  "commentary.delta",
]);

const HISTORY_PROTOCOL_ENVELOPE_FIELDS = new Set([
  "event_id",
  "run_id",
  "message_id",
  "protocol_message_id",
  "sequence",
  "seq",
  "stream_incarnation",
  "event_type",
  "timestamp",
  "emitted_at",
  "schema",
  "trace_ref",
  "causation_event_id",
  "replayable",
]);

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function historyEventData(event: HistoryEvent): Record<string, unknown> {
  return asRecord(event.data);
}

function historyEventIdentity(event: HistoryEvent): string | undefined {
  const data = historyEventData(event);
  if (
    PROTOCOL_MESSAGE_EVENT_TYPES.has(event.event_type) &&
    typeof data.event_id === "string" &&
    data.event_id
  ) {
    return data.event_id;
  }
  if (
    event.event_type === "message:chunk" &&
    data.projection_kind === "assistant_delta" &&
    typeof data.event_id === "string" &&
    data.event_id
  ) {
    return data.event_id;
  }
  return event.id !== undefined && event.id !== null
    ? event.id.toString()
    : undefined;
}

function normalizeProtocolHistoryEventData(
  event: HistoryEvent,
): HistoryEventData | null {
  const raw = historyEventData(event);
  const nestedPayload = asRecord(raw.payload);
  const hasNestedPayload = Object.hasOwn(raw, "payload");
  if (isAssistantTextPartEventType(event.event_type)) {
    const expectedPayloadKeys =
      event.event_type === "message.part.delta"
        ? ["schema_version", "part_id", "delta"]
        : ["schema_version", "part_id", "role"];
    if (
      !hasNestedPayload &&
      Object.keys(raw).some(
        (key) =>
          !expectedPayloadKeys.includes(key) &&
          !HISTORY_PROTOCOL_ENVELOPE_FIELDS.has(key),
      )
    ) {
      return null;
    }
    const payload = hasNestedPayload
      ? nestedPayload
      : Object.fromEntries(
          expectedPayloadKeys
            .filter((key) => Object.hasOwn(raw, key))
            .map((key) => [key, raw[key]]),
        );
    if (!isValidAssistantTextPartPayload(event.event_type, payload)) {
      return null;
    }
    const messageId = raw.message_id ?? raw.protocol_message_id;
    const eventId = raw.event_id;
    const sequence = event.sequence ?? raw.sequence ?? raw.seq;
    const streamIncarnation = raw.stream_incarnation;
    const runId = raw.run_id ?? event.run_id;
    if (
      typeof eventId !== "string" ||
      !eventId ||
      typeof messageId !== "string" ||
      !messageId ||
      typeof runId !== "string" ||
      !runId ||
      typeof sequence !== "number" ||
      !Number.isSafeInteger(sequence) ||
      sequence < 1 ||
      typeof streamIncarnation !== "number" ||
      !Number.isSafeInteger(streamIncarnation) ||
      streamIncarnation < 1
    ) {
      return null;
    }
    return {
      ...(payload as HistoryEventData),
      event_id: eventId,
      run_id: runId,
      message_id: messageId,
      sequence,
      stream_incarnation: streamIncarnation,
      event_type: event.event_type,
      timestamp:
        typeof raw.timestamp === "string"
          ? raw.timestamp
          : typeof raw.emitted_at === "string"
            ? raw.emitted_at
            : event.timestamp,
    };
  }

  const sequence = event.sequence ?? raw.sequence ?? raw.seq;
  const eventId = raw.event_id ?? event.id?.toString();
  const messageId = raw.message_id ?? raw.protocol_message_id;
  const runId = raw.run_id ?? event.run_id;
  const merged: Record<string, unknown> = {
    ...raw,
    ...nestedPayload,
    ...(typeof eventId === "string" ? { event_id: eventId } : {}),
    ...(typeof messageId === "string" ? { message_id: messageId } : {}),
    ...(typeof runId === "string" ? { run_id: runId } : {}),
    ...(typeof sequence === "number" ? { sequence } : {}),
    ...(typeof raw.stream_incarnation === "number"
      ? { stream_incarnation: raw.stream_incarnation }
      : {}),
    event_type: event.event_type,
    timestamp:
      typeof raw.timestamp === "string"
        ? raw.timestamp
        : typeof raw.emitted_at === "string"
          ? raw.emitted_at
          : event.timestamp,
  };
  if (event.event_type === "message.delta") {
    if (typeof merged.delta !== "string") return null;
    merged.content = merged.delta;
    merged.projection_version = CHAT_PUBLIC_PROJECTION_VERSION;
    merged.projection_kind = "assistant_delta";
  } else if (event.event_type === "commentary.delta") {
    if (typeof merged.delta !== "string" || typeof merged.summary_id !== "string") {
      return null;
    }
    merged.content = merged.delta;
  }
  return merged as HistoryEventData;
}

function parseEventTimestamp(
  timestamp: string | undefined,
  fallbackMs: number,
): Date {
  return timestamp ? parseDate(timestamp) : new Date(fallbackMs);
}

function shouldAttachToPreviousAssistant(eventType: string): boolean {
  return eventType === "token:usage";
}

function canAttachToPreviousAssistant(
  event: HistoryEvent,
  message: Message | undefined,
): message is Message {
  return (
    message?.role === "assistant" &&
    Boolean(event.run_id) &&
    message.runId === event.run_id
  );
}

function compatibilityHistoryRank(event: HistoryEvent): number {
  if (event.event_type === "user:message") return -1;
  if (typeof event.sequence === "number") return 0;
  if (event.event_type === "artifact_card") return 1;
  if (
    event.event_type === "message:chunk" ||
    event.event_type === "final_detail"
  ) {
    return 2;
  }
  if (event.event_type === "done") return 3;
  return 0;
}

const DIRECT_HISTORY_PROCESSOR_EVENTS = new Set([
  "message.started",
  "message.completed",
  "message.part.delta",
  "message.part.classified",
  "agent:call",
  "agent:result",
  "thinking",
  "message:chunk",
  "final_detail",
  "sandbox:starting",
  "sandbox:ready",
  "sandbox:error",
  "token:usage",
  "todo:updated",
  "summary",
  "artifact_card",
  "model.completed",
]);
const RETIRED_HISTORY_EVENTS = new Set([
  "approval_required",
  "tool_permission_card",
  "tool_permission_requested",
  "tool_permission_decided",
  "tool_permission_terminalized",
  "subagent_started",
  "subagent_completed",
  "subagent_failed",
  "run_child_created",
]);

/**
 * Persisted compatibility history intentionally exposes its production event
 * type at the outer level. The message processor's durable run-status and
 * execution projection uses the `run_event` envelope, so translate only
 * sequenced persisted rows that have no dedicated visual processor.
 */
function historyProcessorEventType(
  event: HistoryEvent,
  data: HistoryEventData,
): string {
  if (event.event_type === "message.delta") return "message:chunk";
  if (event.event_type === "commentary.delta") return "summary";
  if (
    typeof event.sequence === "number" &&
    !DIRECT_HISTORY_PROCESSOR_EVENTS.has(event.event_type)
  ) {
    return "run_event";
  }
  return event.event_type || data.event_type || "run_event";
}

/**
 * Process a single history event and update message state.
 * Returns updated currentAssistantMessage or new message.
 */
function processHistoryEvent(
  event: HistoryEvent,
  currentAssistantMessage: Message | null,
  processedEventIds: Set<string>,
  opts: ProcessHistoryOptions,
): Message | null {
  const eventType = event.event_type;
  const normalizedProtocolData =
    PROTOCOL_MESSAGE_EVENT_TYPES.has(eventType) ||
    eventType === "commentary.delta"
      ? normalizeProtocolHistoryEventData(event)
      : null;
  const malformedProtocolProjection =
    (isAssistantTextPartEventType(eventType) ||
      eventType === "commentary.delta" ||
      (PROTOCOL_MESSAGE_EVENT_TYPES.has(eventType) &&
        asRecord(event.data).stream_incarnation !== undefined)) &&
    !normalizedProtocolData;
  if (malformedProtocolProjection) {
    if (isAssistantTextPartEventType(eventType)) {
      throw new InvalidAssistantTextPartHistoryError();
    }
    return currentAssistantMessage;
  }
  const eventData =
    normalizedProtocolData || (event.data as HistoryEventData);
  const depth = eventData.depth || 0;
  const agentId = eventData.agent_id;

  if (eventType === "message.started") {
    const messageId = eventData.message_id;
    const streamIncarnation = eventData.stream_incarnation;
    const currentOwner = opts.activeProtocolMessageOwner;
    if (
      currentOwner &&
      (currentOwner.messageId !== messageId ||
        currentOwner.streamIncarnation !== streamIncarnation)
    ) {
      return currentAssistantMessage;
    }
  } else if (
    isAssistantTextPartEventType(eventType) ||
    ((eventType === "message.delta" ||
      eventType === "message.completed" ||
      eventType === "commentary.delta") &&
      eventData.stream_incarnation !== undefined)
  ) {
    const owner = opts.activeProtocolMessageOwner;
    if (
      !owner ||
      eventData.message_id !== owner.messageId ||
      eventData.stream_incarnation !== owner.streamIncarnation
    ) {
      if (isAssistantTextPartEventType(eventType)) {
        throw new InvalidAssistantTextPartHistoryError();
      }
      return currentAssistantMessage;
    }
  }

  // Track processed event IDs using the durable outer id when present, or the
  // public event identity carried by compatibility history.
  const usesProtocolTextIdentity =
    eventType === "message:chunk" &&
    eventData.projection_kind === "assistant_delta";
  const eventIdentity = PROTOCOL_MESSAGE_EVENT_TYPES.has(eventType)
    ? historyEventIdentity({ ...event, data: eventData })
    : usesProtocolTextIdentity &&
        typeof eventData.event_id === "string" &&
        eventData.event_id
      ? eventData.event_id
      : event.id !== undefined && event.id !== null
        ? event.id.toString()
        : undefined;
  const deferEventIdentity =
    PROTOCOL_MESSAGE_EVENT_TYPES.has(eventType) ||
    eventType === "commentary.delta";
  if (eventIdentity && !deferEventIdentity) {
    processedEventIds.add(eventIdentity);
  }

  // Handle user message
  if (eventType === "user:message") {
    return null; // Signal to push current assistant and create user message
  }

  // Skip events that don't contribute to message content
  if (eventType === "metadata" || eventType === "done") {
    return currentAssistantMessage;
  }

  // Retired approval and platform-child events do not create empty messages.
  if (
    RETIRED_HISTORY_EVENTS.has(eventType) ||
    RETIRED_HISTORY_EVENTS.has(String(eventData.event_type || ""))
  ) {
    return currentAssistantMessage;
  }

  // CancelledError with no current message — don't create an empty assistant message
  if (eventType === "error") {
    const errorData = eventData as { type?: string };
    if (errorData.type === "CancelledError" && !currentAssistantMessage) {
      return null;
    }
  }

  // Ensure assistant message exists for other event types
  let msg = currentAssistantMessage;
  if (!msg) {
    const messageId = event.run_id || uuid();
    msg = {
      id: messageId,
      role: "assistant",
      content: "",
      timestamp: parseEventTimestamp(event.timestamp, Date.now()),
      parts: [],
      isStreaming: false,
      runId: event.run_id,
    };
  } else if (event.run_id && !msg.runId) {
    msg = { ...msg, runId: event.run_id };
  }

  // Manage subagent stack
  if (eventType === "agent:call") {
    opts.activeSubagentStack.push({
      agent_id: agentId || "unknown",
      depth,
      message_id: msg.id,
    });
  }

  // Use unified event processor
  const eventDataWithEnvelope = {
    ...eventData,
    event_id:
      deferEventIdentity && typeof eventData.event_id === "string"
        ? eventData.event_id
        : event.id !== undefined && event.id !== null
        ? event.id.toString()
        : eventData.event_id,
    run_id: eventData.run_id || event.run_id,
    event_type: eventData.event_type || event.event_type,
    sequence: event.sequence ?? eventData.sequence,
    timestamp: eventData.timestamp || event.timestamp,
  } as EventData;

  const result = processMessageEvent(
    historyProcessorEventType(event, eventData),
    eventDataWithEnvelope,
    msg.parts || [],
    msg.content,
    msg.toolCalls || [],
    depth,
    opts.activeSubagentStack,
    false, // isStreaming = false for history
    msg.id,
  );

  if (result.accepted === false) {
    if (isAssistantTextPartEventType(eventType)) {
      throw new InvalidAssistantTextPartHistoryError();
    }
    return currentAssistantMessage;
  }
  if (eventIdentity && deferEventIdentity) {
    processedEventIds.add(eventIdentity);
  }
  if (
    eventType === "message.started" &&
    typeof eventData.message_id === "string" &&
    eventData.message_id &&
    typeof eventData.stream_incarnation === "number" &&
    Number.isSafeInteger(eventData.stream_incarnation) &&
    eventData.stream_incarnation >= 1
  ) {
    opts.activeProtocolMessageOwner = {
      messageId: eventData.message_id,
      streamIncarnation: eventData.stream_incarnation,
    };
  }

  // Apply result to message
  msg.parts = result.parts;
  msg.content = result.content;
  msg.toolCalls = result.toolCalls;

  if (result.toolResult) {
    msg.toolResults = [...(msg.toolResults || []), result.toolResult];
  }
  if (result.tokenUsage) {
    msg.tokenUsage = result.tokenUsage;
  }
  if (result.duration !== undefined) {
    msg.duration = result.duration;
  }
  if (result.cancelled) {
    msg.cancelled = true;
  }

  // Pop subagent stack after agent:result
  if (eventType === "agent:result") {
    const stackIndex = opts.activeSubagentStack.findIndex(
      (item) =>
        item.agent_id === (agentId || "unknown") && item.message_id === msg.id,
    );
    if (stackIndex !== -1) {
      opts.activeSubagentStack.splice(stackIndex, 1);
    }
  }

  return msg;
}

/**
 * Reconstruct messages from history events.
 */
export function reconstructMessagesFromEvents(
  events: HistoryEvent[],
  processedEventIds: Set<string>,
  opts: ProcessHistoryOptions,
): Message[] {
  // A rejected versioned projection must not partially advance the caller's
  // replay identity set or subagent/message-owner state.
  const pendingProcessedEventIds = new Set(processedEventIds);
  const processingOptions: ProcessHistoryOptions = {
    activeSubagentStack: [...opts.activeSubagentStack],
    activeProtocolMessageOwner: null,
  };
  // A run's compatibility projection is ordered by persisted sequence, then
  // artifact/final payload, then its synthetic terminal. Across runs, retain
  // the backend's authoritative creation-order grouping: completion timestamps
  // from an older overlapping run are not a run-generation signal.
  // Deduplicate one authoritative history response before ordering. Live-event
  // identity tracking cannot be reused here because history reconstructs state
  // from scratch and may legitimately contain events already seen live.
  const seenHistoryEventIds = new Set<string>();
  const uniqueEvents = events.filter((event) => {
    const eventIdentity = historyEventIdentity(event);
    if (eventIdentity && seenHistoryEventIds.has(eventIdentity)) return false;
    if (eventIdentity) seenHistoryEventIds.add(eventIdentity);
    return true;
  });
  const runOrder = new Map<string, number>();
  uniqueEvents.forEach((event) => {
    if (event.run_id && !runOrder.has(event.run_id)) {
      runOrder.set(event.run_id, runOrder.size);
    }
  });
  const sortedEvents = uniqueEvents.map((event, index) => ({ event, index })).sort((a, b) => {
    const runOrderA = a.event.run_id
      ? (runOrder.get(a.event.run_id) ?? a.index)
      : runOrder.size + a.index;
    const runOrderB = b.event.run_id
      ? (runOrder.get(b.event.run_id) ?? b.index)
      : runOrder.size + b.index;
    if (runOrderA !== runOrderB) {
      return runOrderA - runOrderB;
    }
    const eventA = a.event;
    const eventB = b.event;
    if (eventA.run_id && eventA.run_id === eventB.run_id) {
      const sequenceA = typeof eventA.sequence === "number" ? eventA.sequence : null;
      const sequenceB = typeof eventB.sequence === "number" ? eventB.sequence : null;
      if (sequenceA !== null && sequenceB !== null && sequenceA !== sequenceB) {
        return sequenceA - sequenceB;
      }
      const rankA = compatibilityHistoryRank(eventA);
      const rankB = compatibilityHistoryRank(eventB);
      if (rankA !== rankB) {
        return rankA - rankB;
      }
    }
    const timeA = parseEventTimestamp(eventA.timestamp, 0).getTime();
    const timeB = parseEventTimestamp(eventB.timestamp, 0).getTime();
    return timeA === timeB ? a.index - b.index : timeA - timeB;
  }).map(({ event }) => event);

  const reconstructedMessages: Message[] = [];
  let currentAssistantMessage: Message | null = null;

  for (const event of sortedEvents) {
    const eventType = event.event_type;
    const eventData = event.data as HistoryEventData;

    if (
      currentAssistantMessage?.runId &&
      event.run_id &&
      currentAssistantMessage.runId !== event.run_id
    ) {
      reconstructedMessages.push(currentAssistantMessage);
      currentAssistantMessage = null;
      processingOptions.activeSubagentStack.splice(
        0,
        processingOptions.activeSubagentStack.length,
      );
      processingOptions.activeProtocolMessageOwner = null;
    }

    // Handle user message separately
    if (eventType === "user:message") {
      if (currentAssistantMessage) {
        reconstructedMessages.push(currentAssistantMessage);
        currentAssistantMessage = null;
      }
      const userAttachments = convertAttachments(eventData.attachments);
      reconstructedMessages.push({
        id: resolveUserMessageId(event, eventData),
        role: "user",
        content: eventData.content || "",
        timestamp: parseEventTimestamp(event.timestamp, Date.now()),
        attachments: userAttachments,
        lockedSkillLabel:
          typeof eventData.locked_skill_label === "string" &&
          eventData.locked_skill_label.trim()
            ? eventData.locked_skill_label.trim()
            : undefined,
        runId: event.run_id,
      });
      processingOptions.activeProtocolMessageOwner = null;
      continue;
    }

    // Handle user cancel
    if (eventType === "user:cancel") {
      if (currentAssistantMessage) {
        const clearedParts = clearAllLoadingStates(
          currentAssistantMessage.parts || [],
        );
        // Also set result on pending tools for history display
        const updatedParts = clearedParts.map((part): MessagePart => {
          if (part.type === "tool" && part.cancelled && !part.result) {
            return {
              ...part,
              result: i18n.t("chat.cancelled"),
              success: false,
            };
          }
          return part;
        });
        const updatedMessage = {
          ...currentAssistantMessage,
          isStreaming: false,
          cancelled: true,
          parts: [...updatedParts, { type: "cancelled" as const }],
        };
        reconstructedMessages.push(updatedMessage);
      } else {
        reconstructedMessages.push({
          id: uuid(),
          role: "assistant",
          content: "",
          timestamp: parseEventTimestamp(event.timestamp, Date.now()),
          parts: [{ type: "cancelled" }],
          runId: event.run_id,
        });
      }
      currentAssistantMessage = null;
      processingOptions.activeProtocolMessageOwner = null;
      continue;
    }

    if (
      !currentAssistantMessage &&
      shouldAttachToPreviousAssistant(eventType)
    ) {
      const lastMessageIndex = reconstructedMessages.length - 1;
      const lastMessage = reconstructedMessages[lastMessageIndex];
      if (canAttachToPreviousAssistant(event, lastMessage)) {
        const updatedMessage = processHistoryEvent(
          event,
          lastMessage,
          pendingProcessedEventIds,
          processingOptions,
        );
        if (updatedMessage) {
          reconstructedMessages[lastMessageIndex] = updatedMessage;
        }
        continue;
      }
    }

    // Process other events
    currentAssistantMessage = processHistoryEvent(
      event,
      currentAssistantMessage,
      pendingProcessedEventIds,
      processingOptions,
    );
  }

  if (currentAssistantMessage) {
    reconstructedMessages.push(currentAssistantMessage);
  }

  processedEventIds.clear();
  pendingProcessedEventIds.forEach((eventId) => processedEventIds.add(eventId));
  opts.activeSubagentStack.splice(
    0,
    opts.activeSubagentStack.length,
    ...processingOptions.activeSubagentStack,
  );
  opts.activeProtocolMessageOwner = processingOptions.activeProtocolMessageOwner;

  return reconstructedMessages;
}

/** True when a Run has public answer text or a usable artifact. */
export function hasDisplayableRunAnswer(
  messages: readonly Message[],
  runId: string,
): boolean {
  return messages.some((message) => {
    if (message.role !== "assistant" || message.runId !== runId) return false;
    const parts = message.parts || [];
    const hasTextPart = parts.some(
      (part) =>
        part.type === "text" &&
        !part.depth &&
        Boolean(part.content.trim()) &&
        assistantTextPartRole(part) !== null &&
        assistantTextPartRole(part) !== "pending" &&
        assistantTextPartRole(part) !== "work",
    );
    const hasDeliverableArtifact = parts.some(
      (part) =>
        part.type === "artifact" &&
        Boolean(part.artifact_id.trim()) &&
        part.status !== "failed" &&
        (part.status === "ready" ||
          Boolean(part.download_url?.trim()) ||
          Boolean(part.preview_url?.trim())),
    );
    const hasTerminalDetail = parts.some(
      (part) => part.type === "run_status" &&
        Boolean(getPublicTerminalPresentationDefinition(part.event_type)),
    );
    return (
      hasTextPart ||
      hasDeliverableArtifact ||
      (!hasTerminalDetail &&
        !hasMarkedAssistantTextParts(parts) &&
        Boolean(message.content.trim()))
    );
  });
}

/** Preserve the public body while showing that persisted result recovery failed. */
export function withUnavailableTerminalResult(
  messages: Message[],
  runId: string,
  messageId: string,
): Message[] {
  const card: MessagePart = {
    type: "run_status",
    event_id: `terminal-result-unavailable:${runId}`,
    event_type: "terminal_result_unavailable",
    stage: "agent",
    message: i18n.t("chat.runTerminal.terminalResultUnavailable", {
      defaultValue: "任务终态已确认，但结果暂时无法加载。请刷新当前会话。",
    }),
    severity: "warning",
  };
  return ensureTerminalAssistantSegment(messages, runId, messageId).map((message) => {
    if (message.role !== "assistant" || message.runId !== runId) return message;
    const parts = clearAllLoadingStates(message.parts || []);
    return {
      ...message,
      isStreaming: false,
      isSynchronizing: false,
      parts: parts.some((part) => part.type === "run_status" && part.event_id === card.event_id)
        ? parts
        : [...parts, card],
    };
  });
}

const MAX_OLDER_SUCCESSFUL_RUN_RECOVERIES = 8;

interface OlderSuccessfulRunHistory {
  events?: HistoryEvent[];
  terminal_run_statuses?: Record<string, string>;
}

/** Backfill missing answers for a bounded set of older successful Runs. */
export async function recoverOlderSuccessfulRunHistory({
  messages,
  currentRunId,
  terminalRunStatuses,
  isCurrent,
  loadExactRunHistory,
  onUnavailable,
  onRecovered,
}: {
  messages: readonly Message[];
  currentRunId: string | null;
  terminalRunStatuses?: Record<string, string>;
  isCurrent: () => boolean;
  loadExactRunHistory: (runId: string) => Promise<OlderSuccessfulRunHistory>;
  onUnavailable: (runId: string) => void;
  onRecovered: (runId: string, messages: Message[]) => void;
}): Promise<void> {
  const incompleteOlderSucceededRuns = Object.entries(
    terminalRunStatuses || {},
  )
    .filter(
      ([runId, status]) =>
        runId !== currentRunId &&
        status === "succeeded" &&
        !hasDisplayableRunAnswer(messages, runId),
    )
    .map(([runId]) => runId);
  const olderRunsToRecover = incompleteOlderSucceededRuns.slice(
    -MAX_OLDER_SUCCESSFUL_RUN_RECOVERIES,
  );
  if (
    incompleteOlderSucceededRuns.length > olderRunsToRecover.length &&
    isCurrent()
  ) {
    incompleteOlderSucceededRuns
      .slice(0, -MAX_OLDER_SUCCESSFUL_RUN_RECOVERIES)
      .forEach(onUnavailable);
  }

  await Promise.all(
    olderRunsToRecover.map(async (runId) => {
      if (!isCurrent()) return;
      let exactMessages: Message[];
      let exactRunHistory: OlderSuccessfulRunHistory;
      try {
        exactRunHistory = await loadExactRunHistory(runId);
        if (!isCurrent()) return;
        exactMessages = reconstructMessagesFromEvents(
          exactRunHistory.events || [],
          new Set<string>(),
          { activeSubagentStack: [] },
        );
      } catch {
        if (isCurrent()) onUnavailable(runId);
        return;
      }
      if (!isCurrent()) return;
      if (
        exactRunHistory.terminal_run_statuses?.[runId] !== "succeeded" ||
        !hasDisplayableRunAnswer(exactMessages, runId)
      ) {
        onUnavailable(runId);
        return;
      }
      onRecovered(
        runId,
        exactMessages.map((message) => normalizeMessageTextLogicalIds(message)),
      );
    }),
  );
}

function hydratedMessageIdentity(message: Message, runId: string): string | null {
  if (message.runId !== runId) return null;
  if (message.role === "assistant") return `assistant:${runId}`;
  return message.id ? `${message.role}:${message.id}` : null;
}

/** Replace exactly one complete run segment using stable message/run identities. */
export function mergeHydratedRunSegment(
  messages: Message[],
  hydratedMessages: Message[],
  runId: string,
): Message[] {
  const identities: string[] = [];
  const authoritativeByIdentity = new Map<string, Message>();
  for (const message of hydratedMessages) {
    const identity = hydratedMessageIdentity(message, runId);
    if (!identity) continue;
    if (!authoritativeByIdentity.has(identity)) identities.push(identity);
    authoritativeByIdentity.set(identity, message);
  }
  const authoritativeSegment = identities
    .map((identity) => authoritativeByIdentity.get(identity))
    .filter((message): message is Message => Boolean(message));
  if (authoritativeSegment.length === 0) return messages;
  const hasAuthoritativePublicText = authoritativeSegment.some(
    (message) =>
      message.role === "assistant" &&
      message.runId === runId &&
      (message.parts || []).some(
        (part) =>
          part.type === "text" &&
          Boolean(part.content) &&
          assistantTextPartRole(part) !== null,
      ),
  );

  // Failed/cancelled history can be an exact but partial public projection.
  // Prefer its content and role for every stable source it contains; retain
  // only safe live sources that are genuinely absent from that history.
  if (
    !hasDisplayableRunAnswer(authoritativeSegment, runId) ||
    !hasAuthoritativePublicText
  ) {
    const previous = messages.find(
      (message) => message.role === "assistant" && message.runId === runId,
    );
    const recoveredIndex = authoritativeSegment.findIndex((message) => message.role === "assistant");
    const recovered = authoritativeSegment[recoveredIndex];
    if (previous && recovered) {
      const authoritativeTextParts = (recovered.parts || []).filter(
        (part) =>
          part.type === "text" &&
          Boolean(part.content) &&
          assistantTextPartRole(part) !== null,
      );
      const authoritativePartIds = new Set(
        authoritativeTextParts.flatMap((part) =>
          part.type === "text" && part.public_part_id
            ? [part.public_part_id]
            : [],
        ),
      );
      const previousTextParts = (previous.parts || []).filter(
        (part) =>
          part.type === "text" &&
          Boolean(part.content) &&
          assistantTextPartRole(part) !== null,
      );
      const missingPreviousTextParts = previousTextParts.filter((part) => {
        if (authoritativeTextParts.length === 0) return true;
        return (
          part.type === "text" &&
          Boolean(part.public_part_id) &&
          !authoritativePartIds.has(part.public_part_id!)
        );
      });
      const authoritativeArtifactIds = new Set(
        (recovered.parts || []).flatMap((part) =>
          part.type === "artifact" ? [part.artifact_id] : [],
        ),
      );
      const missingPreviousArtifacts = (previous.parts || []).filter(
        (part) =>
          part.type === "artifact" &&
          !authoritativeArtifactIds.has(part.artifact_id),
      );
      const recoveredOtherParts = (recovered.parts || []).filter(
        (part) => part.type !== "text" && part.type !== "artifact",
      );
      const recoveredHasTerminalDetail = (recovered.parts || []).some(
        (part) =>
          part.type === "run_status" &&
          Boolean(getPublicTerminalPresentationDefinition(part.event_type)),
      );
      const recoveredHasLegacyAnswer =
        Boolean(recovered.content.trim()) && !recoveredHasTerminalDetail;
      const parts = [
        ...authoritativeTextParts,
        ...missingPreviousTextParts,
        ...missingPreviousArtifacts,
        ...recoveredOtherParts,
      ];
      const attachments = [...(recovered.attachments || [])];
      const attachmentIds = new Set(attachments.map((attachment) => attachment.id));
      for (const attachment of previous.attachments || []) {
        if (!attachmentIds.has(attachment.id)) {
          attachments.push(attachment);
          attachmentIds.add(attachment.id);
        }
      }
      const hasVersionedText = hasMarkedAssistantTextParts(parts);
      authoritativeSegment[recoveredIndex] = {
        ...recovered,
        content: hasVersionedText
          ? composeAssistantPreviewText(parts)
          : authoritativeTextParts.length > 0 || recoveredHasLegacyAnswer
            ? recovered.content
            : previous.content,
        attachments,
        parts,
      };
    }
  }

  const firstIndex = messages.findIndex(
    (message) => message.runId === runId,
  );
  if (firstIndex < 0) {
    return [...messages, ...authoritativeSegment];
  }
  const merged: Message[] = [];
  let inserted = false;
  messages.forEach((message, index) => {
    if (message.runId === runId) {
      if (!inserted && index === firstIndex) {
        merged.push(...authoritativeSegment);
        inserted = true;
      }
      return;
    }
    merged.push(message);
  });
  return merged;
}

/** Ensure a confirmed terminal run has an assistant presentation owner. */
export function ensureTerminalAssistantSegment(
  messages: Message[],
  runId: string,
  messageId: string,
): Message[] {
  if (
    messages.some(
      (message) => message.role === "assistant" && message.runId === runId,
    )
  ) {
    return messages;
  }
  const lastRunIndex = messages.reduce(
    (lastIndex, message, index) =>
      message.runId === runId ? index : lastIndex,
    -1,
  );
  const assistant: Message = {
    id: messageId || runId,
    runId,
    role: "assistant",
    content: "",
    timestamp:
      lastRunIndex >= 0 ? messages[lastRunIndex].timestamp : new Date(),
    isStreaming: false,
    parts: [],
  };
  if (lastRunIndex < 0) return [...messages, assistant];
  return [
    ...messages.slice(0, lastRunIndex + 1),
    assistant,
    ...messages.slice(lastRunIndex + 1),
  ];
}

export interface RunningAssistantPreparationResult {
  messages: Message[];
  streamingMessageId: string;
}

export function prepareMessagesForRunningRun(
  messages: Message[],
  runId: string,
  createId: () => string = () => uuid(),
): RunningAssistantPreparationResult {
  const existingAssistant = [...messages]
    .reverse()
    .find((message) => message.role === "assistant" && message.runId === runId);

  if (existingAssistant) {
    return {
      streamingMessageId: existingAssistant.id,
      messages: messages.map((message) =>
        message.id === existingAssistant.id
          ? { ...message, isStreaming: true, isSynchronizing: false }
          : message,
      ),
    };
  }

  const streamingMessageId = createId();
  return {
    streamingMessageId,
    messages: [
      ...messages,
      {
        id: streamingMessageId,
        role: "assistant",
        content: "",
        timestamp: new Date(),
        parts: [],
        isStreaming: true,
        runId,
      },
    ],
  };
}

/**
 * Get the last event timestamp from sorted events.
 */
export function getLastEventTimestamp(events: HistoryEvent[]): Date | null {
  if (events.length === 0) return null;
  let lastEvent: HistoryEvent | null = null;
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i].timestamp) {
      lastEvent = events[i];
      break;
    }
  }
  return lastEvent?.timestamp ? parseDate(lastEvent.timestamp) : null;
}
