/**
 * Stream event handlers for useAgent hook
 * Handles all incoming SSE events and updates messages accordingly.
 *
 * Message transformation logic is unified in processMessageEvent (messageParts.ts).
 * This file handles: SSE parsing, duplicate detection, subagent stack management,
 * and React state updates (side effects).
 */

import type { Message, MessagePart } from "../../types";
import { uuid } from "../../utils/uuid";
import { sessionApi } from "../../services/api";
import i18n from "../../i18n";
import { translateBackendError } from "../../utils/backendErrors";
import { parseDate } from "../../utils/datetime";
import type {
  StreamEvent,
  EventData,
  SubagentStackItem,
  UseAgentOptions,
} from "./types";
import {
  isPublicExecutionEvent,
  isAssistantTextProjection,
  isSequencedPublicChatEvent,
  PUBLIC_EXECUTION_EVENT_TYPES,
} from "./types";
import { clearAllLoadingStates } from "./messageParts";
import { convertAttachments, processMessageEvent } from "./eventProcessor";
import {
  collapsePublicExecutionSteps,
  type PublicStreamPresentation,
  type PublicStreamPresentationOwner,
} from "./publicStreamPresentation";
import { comparePublicRunStreamCursors } from "./publicRunStreamV3";
import {
  terminalRunStatusFromEvent,
  type TerminalRunStatus,
} from "./runLifecycle";
import {
  adaptPublicRunStreamEventV4,
  projectV4EventToLegacyHandler,
  type V4AdapterBinding,
  type V4PublicEvent,
  type V4SseFrame,
} from "../../components/chat/assistant-ui/publicEventAdapter";

/**
 * Context passed to event handler
 */
export interface EventHandlerContext {
  options?: UseAgentOptions;
  sessionIdRef: React.MutableRefObject<string | null>;
  currentRunIdRef: React.MutableRefObject<string | null>;
  processedEventIdsRef: React.MutableRefObject<Set<string>>;
  acceptedRunEventSequenceRef?: React.MutableRefObject<AcceptedRunEventSequence>;
  acceptedStreamCursorRef?: React.MutableRefObject<AcceptedStreamCursor>;
  /** v4 terminal fence: stream.end is accepted only after its terminal event. */
  v4TerminalEventIdsRef?: React.MutableRefObject<Set<string>>;
  v4TerminalFenceRef?: React.MutableRefObject<V4TerminalFence | null>;
  lastHistoryTimestampRef: React.MutableRefObject<Date | null>;
  activeSubagentStackRef: React.MutableRefObject<SubagentStackItem[]>;
  streamVersionRef: React.MutableRefObject<number>;
  setSessionId: (id: string) => void;
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  setConnectionStatus: (status: string) => void;
  setIsInitializingSandbox: (loading: boolean) => void;
  setSandboxError: (error: string | null) => void;
  onRunTerminal?: (
    runId: string,
    status: TerminalRunStatus,
    messageId: string,
    onAccepted?: () => void,
  ) => boolean;
  onRunStatusUnavailable?: (runId: string, messageId: string) => boolean;
  dismissQueueToast?: () => void;
  publicStreamPresentation?: PublicStreamPresentation;
}

export interface V4TerminalFence {
  sessionId: string;
  runId: string;
  streamIncarnation: number;
  generation?: number;
  terminalEventId: string;
}

function matchesV4TerminalFence(
  event: V4PublicEvent,
  ctx: EventHandlerContext,
  terminalEventId: string | undefined,
): boolean {
  const fence = ctx.v4TerminalFenceRef?.current;
  return Boolean(
    fence &&
      terminalEventId &&
      fence.terminalEventId === terminalEventId &&
      fence.sessionId === ctx.sessionIdRef.current &&
      fence.runId === ctx.currentRunIdRef.current &&
      fence.runId === event.runId &&
      fence.streamIncarnation === event.streamIncarnation &&
      (fence.generation === undefined || fence.generation === event.generation),
  );
}

function terminalFenceFromEvent(
  event: V4PublicEvent,
  ctx: EventHandlerContext,
  terminalEventId: string,
  onAccepted: () => void,
): () => void {
  return () => {
    const sessionId = ctx.sessionIdRef.current;
    if (!sessionId || ctx.currentRunIdRef.current !== event.runId) return;
    ctx.v4TerminalFenceRef!.current = {
      sessionId,
      runId: event.runId,
      streamIncarnation: event.streamIncarnation,
      generation: event.generation,
      terminalEventId,
    };
    onAccepted();
  };
}

/** Durable, bounded public-event cursor for the active session/run stream. */
export interface AcceptedRunEventSequence {
  sessionId: string | null;
  runId: string | null;
  sequence: number | null;
}

export interface AcceptedStreamCursor {
  sessionId: string | null;
  runId: string | null;
  eventId: string | null;
  streamIncarnation?: number | null;
}

/** The connection generation that authorizes a runless stream frame. */
export interface StreamEventBinding {
  sessionId: string;
  runId: string;
  streamVersion: number;
}

function presentationOwner(
  binding: StreamEventBinding | undefined,
  messageId: string,
): PublicStreamPresentationOwner | null {
  return binding
    ? {
        sessionId: binding.sessionId,
        runId: binding.runId,
        assistantMessageId: messageId,
        streamVersion: binding.streamVersion,
      }
    : null;
}

function compareTransportCursors(left: string, right: string): number | null {
  const structured = comparePublicRunStreamCursors(left, right);
  if (structured !== null) return structured;
  const parseRedisId = (value: string): [bigint, bigint] | null => {
    const match = /^(0|[1-9][0-9]*)-(0|[1-9][0-9]*)$/.exec(value);
    return match ? [BigInt(match[1]), BigInt(match[2])] : null;
  };
  const leftId = parseRedisId(left);
  const rightId = parseRedisId(right);
  if (!leftId || !rightId) return null;
  if (leftId[0] !== rightId[0]) return leftId[0] < rightId[0] ? -1 : 1;
  if (leftId[1] === rightId[1]) return 0;
  return leftId[1] < rightId[1] ? -1 : 1;
}

const MESSAGE_EVENTS = new Set<string>([
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
  "run_event",
  "execution_step",
  "execution_progress",
  "execution_step_completed",
  "execution_step_failed",
  "artifact_card",
  "error",
]);

const SIDE_EFFECT_EVENTS = new Set<string>([
  "metadata",
  "user:message",
  "user:cancel",
  "complete",
  "done",
  "end",
  "stream_open",
  "skills:changed",
  "heartbeat",
]);

function runEventSequence(data: EventData): number | null {
  return typeof data.sequence === "number" &&
    Number.isSafeInteger(data.sequence) &&
    data.sequence >= 0
    ? data.sequence
    : null;
}

function dismissQueueToast(ctx: EventHandlerContext): void {
  if (ctx.dismissQueueToast) {
    ctx.dismissQueueToast();
    return;
  }
  void import("react-hot-toast").then(({ default: toast }) => {
    toast.dismiss("chat-queue");
  });
}

/**
 * Additive v4 production seam. The existing handler remains the sole owner of
 * message, status, artifact, cursor, and terminal side effects.
 */
export function handlePublicRunStreamEventV4(
  event: V4PublicEvent,
  messageId: string,
  ctx: EventHandlerContext,
  binding?: StreamEventBinding,
  onCommitted?: (semanticApplied: boolean) => void,
): boolean {
  const terminalEventId =
    event.eventType === "stream.end" || event.eventType.startsWith("run.")
      ? ((event.event.payload as unknown as Record<string, unknown>).terminal_event_id as string | undefined)
      : undefined;
  const terminalStatus = terminalRunStatusFromEvent(
    event.eventType,
    event.event as unknown as Record<string, unknown>,
  );
  if (terminalStatus && event.eventType !== "stream.end") {
    const owner = presentationOwner(binding, messageId);
    if (owner) ctx.publicStreamPresentation?.flush(owner);
    if (!terminalEventId || !ctx.v4TerminalFenceRef || !ctx.onRunTerminal) return false;
    const accepted = ctx.onRunTerminal(
      event.runId,
      terminalStatus,
      messageId,
      terminalFenceFromEvent(event, ctx, terminalEventId, () => undefined),
    );
    if (!accepted) return false;
    onCommitted?.(false);
    return true;
  }
  if (event.eventType === "stream.end") {
    const fenced = matchesV4TerminalFence(event, ctx, terminalEventId);
    const legacyFenced = Boolean(
      ctx.v4TerminalEventIdsRef &&
      terminalEventId &&
      ctx.v4TerminalEventIdsRef.current.has(terminalEventId),
    );
    if (!fenced && !legacyFenced) return false;
  }
  const projected = projectV4EventToLegacyHandler(event, messageId);
  if (!projected) return false;
  const accepted = handleStreamEvent(
    projected.streamEvent,
    projected.messageId,
    event.transportCursor,
    event.emittedAt,
    event.eventType === "stream.end" && ctx.v4TerminalFenceRef
      ? { ...ctx, v4TerminalEventIdsRef: undefined }
      : ctx,
    binding,
    onCommitted,
  );
  if (accepted && terminalEventId && ctx.v4TerminalEventIdsRef) {
    ctx.v4TerminalEventIdsRef.current.add(terminalEventId);
  }
  return accepted;
}

/** Call-ready v4 composition seam: validate, route gaps, then delegate once. */
export function handlePublicRunStreamFrameV4({
  frame,
  adapterBinding,
  messageId,
  ctx,
  binding,
  onGap,
  onCommitted,
}: {
  frame: V4SseFrame;
  adapterBinding: V4AdapterBinding;
  messageId: string;
  ctx: EventHandlerContext;
  binding?: StreamEventBinding;
  onGap?: (event: V4PublicEvent) => void;
  onCommitted?: (semanticApplied: boolean) => void;
}): boolean {
  const event = adaptPublicRunStreamEventV4(frame, adapterBinding);
  if (!event) return false;
  if (event.eventType === "stream.gap") {
    onGap?.(event);
    return false;
  }
  return handlePublicRunStreamEventV4(
    event,
    messageId,
    ctx,
    binding,
    onCommitted,
  );
}

/**
 * Handle incoming SSE events
 */
export function handleStreamEvent(
  event: StreamEvent,
  messageId: string,
  eventId: string,
  eventTimestamp: string | undefined,
  ctx: EventHandlerContext,
  binding?: StreamEventBinding,
  onCommitted?: (semanticApplied: boolean) => void,
): boolean {
  console.log("[handleStreamEvent] Received event:", {
    eventType: event.event,
    messageId,
    eventId,
  });

  // A bound SSE connection is the authority for both run ownership and
  // generation. Generic callers cannot bind runless terminal frames.
  if (
    binding &&
    (ctx.streamVersionRef.current !== binding.streamVersion ||
      ctx.sessionIdRef.current !== binding.sessionId ||
      ctx.currentRunIdRef.current !== binding.runId)
  ) {
    return false;
  }

  const eventType = event.event;
  let data: EventData;
  try {
    const parsed = JSON.parse(event.data);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      return false;
    }
    data = parsed as EventData;
  } catch {
    return false;
  }

  const eventRunId =
    typeof data.run_id === "string" && data.run_id.trim()
      ? data.run_id
      : null;
  if (eventRunId && ctx.currentRunIdRef.current !== eventRunId) {
    return false;
  }
  if (binding && eventRunId && eventRunId !== binding.runId) {
    return false;
  }

  const cursorSessionId = binding?.sessionId ?? ctx.sessionIdRef.current;
  const cursorRunId = binding?.runId ?? eventRunId;
  const acceptedCursorBeforeEffects = ctx.acceptedStreamCursorRef?.current;
  if (
    acceptedCursorBeforeEffects &&
    acceptedCursorBeforeEffects.sessionId === cursorSessionId &&
    acceptedCursorBeforeEffects.runId === cursorRunId &&
    acceptedCursorBeforeEffects.eventId
  ) {
    const cursorComparison = compareTransportCursors(
      eventId,
      acceptedCursorBeforeEffects.eventId,
    );
    if (cursorComparison !== null && cursorComparison <= 0) {
      return false;
    }
  }

  const semanticEventId =
    typeof data.event_id === "string" && data.event_id.trim()
      ? data.event_id
      : eventId;
  if (ctx.processedEventIdsRef.current.has(semanticEventId)) {
    // The reducer already owns this semantic event, but a later Redis entry is
    // still valid transport progress and may advance Last-Event-ID.
    onCommitted?.(false);
    return false;
  }

  const terminalStatus = terminalRunStatusFromEvent(
    eventType,
    data as unknown as Record<string, unknown>,
  );
  // Only a generation-bound SSE connection may bind a runless terminal frame.
  // Generic event handling, including history replay, must have an explicit id.
  if (terminalStatus && !eventRunId) {
    return false;
  }
  if (
    PUBLIC_EXECUTION_EVENT_TYPES.has(eventType as never) &&
    !isPublicExecutionEvent(eventType, data)
  ) {
    return false;
  }

  if (!SIDE_EFFECT_EVENTS.has(eventType) && !MESSAGE_EVENTS.has(eventType)) {
    console.warn("[SSE] Unhandled event type:", eventType);
    return false;
  }

  if (eventTimestamp && ctx.lastHistoryTimestampRef.current) {
    const eventTime = parseDate(eventTimestamp);
    const historyTime = ctx.lastHistoryTimestampRef.current;
    if (eventTime <= historyTime) {
      onCommitted?.(false);
      return false;
    }
  }

  const sequencedPublicEvent = isSequencedPublicChatEvent(eventType, data);
  const progressSequence = sequencedPublicEvent ? runEventSequence(data) : null;
  const progressSessionId = binding?.sessionId ?? ctx.sessionIdRef.current;
  const progressRunId = binding?.runId ?? eventRunId;
  const acceptedProgress = ctx.acceptedRunEventSequenceRef?.current;
  // Sequenced public run/projection frames can only be accepted through the
  // durable cursor. A partial caller without that cursor must fail closed.
  if (sequencedPublicEvent && progressSequence !== null && !acceptedProgress) {
    return false;
  }
  if (
    sequencedPublicEvent &&
    progressSequence !== null &&
    progressSessionId &&
    progressRunId &&
    acceptedProgress &&
    acceptedProgress.sessionId === progressSessionId &&
    acceptedProgress.runId === progressRunId &&
    acceptedProgress.sequence !== null &&
    progressSequence <= acceptedProgress.sequence
  ) {
    if (progressSequence === acceptedProgress.sequence) {
      onCommitted?.(false);
    }
    return false;
  }

  let committed = false;
  const commitAcceptedEvent = () => {
    if (committed) return;
    committed = true;
    ctx.processedEventIdsRef.current.add(semanticEventId);
    if (ctx.processedEventIdsRef.current.size > 10_000) {
      ctx.processedEventIdsRef.current.clear();
      ctx.processedEventIdsRef.current.add(semanticEventId);
    }
    if (
      sequencedPublicEvent &&
      progressSequence !== null &&
      progressSessionId &&
      progressRunId &&
      ctx.acceptedRunEventSequenceRef
    ) {
      const accepted = ctx.acceptedRunEventSequenceRef.current;
      const isSameRun =
        accepted !== null &&
        accepted.sessionId === progressSessionId &&
        accepted.runId === progressRunId;
      if (
        isSameRun &&
        accepted !== null &&
        accepted.sequence !== null &&
        progressSequence <= accepted.sequence
      ) {
        onCommitted?.(true);
        return;
      }
      ctx.acceptedRunEventSequenceRef.current = {
        sessionId: progressSessionId,
        runId: progressRunId,
        sequence: progressSequence,
      };
    }
    onCommitted?.(true);
  };

  const commitTransportOnly = () => {
    if (committed) return;
    committed = true;
    onCommitted?.(false);
  };

  if (terminalStatus && eventRunId) {
    const owner = presentationOwner(binding, messageId);
    if (owner) ctx.publicStreamPresentation?.flush(owner);
    if (ctx.onRunTerminal?.(eventRunId, terminalStatus, messageId)) {
      commitAcceptedEvent();
      return true;
    }
  }

  const depth = data.depth || 0;

  // Events handled entirely by side effects (no message transformation)
  switch (eventType) {
    case "metadata": {
      if (
        data.session_id &&
        !ctx.sessionIdRef.current &&
        (!binding || ctx.streamVersionRef.current === binding.streamVersion)
      ) {
        ctx.setSessionId(data.session_id);
      }
      commitAcceptedEvent();
      return true;
    }

    case "user:message": {
      handleUserMessage(data, messageId, eventTimestamp, ctx);
      commitAcceptedEvent();
      return true;
    }

    case "user:cancel": {
      dismissQueueToast(ctx);
      const owner = presentationOwner(binding, messageId);
      if (owner) ctx.publicStreamPresentation?.flush(owner);
      handleError(data, messageId, ctx, true, { keepConnectionOpen: true });
      commitAcceptedEvent();
      return true;
    }

    case "complete":
    case "done": {
      dismissQueueToast(ctx);
      const owner = presentationOwner(binding, messageId);
      if (owner) ctx.publicStreamPresentation?.flush(owner);
      ctx.setMessages((prev) =>
        prev.map((m) =>
          m.id === messageId
            ? {
                ...m,
                isStreaming: false,
                parts: collapsePublicExecutionSteps(
                  clearAllLoadingStates(m.parts || []),
                ),
              }
            : m,
        ),
      );
      ctx.publicStreamPresentation?.invalidate();
      ctx.setConnectionStatus("disconnected");
      // AI 回复完成，用户正在查看当前 session，立即标记为已读
      const activeSessionId = ctx.sessionIdRef.current;
      if (activeSessionId) {
        sessionApi.markRead(activeSessionId).catch(() => {});
      }
      ctx.options?.onStreamDone?.();
      commitAcceptedEvent();
      return true;
    }

    case "stream_open": {
      if (ctx.dismissQueueToast) {
        ctx.dismissQueueToast();
      } else {
        void import("react-hot-toast").then(({ default: toast }) => {
          toast.dismiss("chat-queue");
          toast.success(i18n.t("chat.queueStart"), { duration: 2000 });
        });
      }
      commitAcceptedEvent();
      return true;
    }

    case "heartbeat": {
      // Heartbeats have already passed the session/run/generation fence above.
      // They are liveness signals, not assistant text or a fake progress card.
      commitAcceptedEvent();
      return true;
    }

    case "end": {
      const terminalEventId =
        typeof data.payload === "object" && data.payload !== null && !Array.isArray(data.payload) &&
        typeof data.payload.terminal_event_id === "string"
          ? data.payload.terminal_event_id
          : undefined;
      if (
        ctx.v4TerminalEventIdsRef &&
        terminalEventId !== undefined &&
        !ctx.v4TerminalEventIdsRef.current.has(terminalEventId)
      ) {
        return false;
      }
      commitAcceptedEvent();
      return true;
    }

    case "skills:changed": {
      if (ctx.options?.onSkillAdded) {
        const action = (data.action as string) || "updated";
        const description =
          action === "created"
            ? i18n.t("chat.skillCreated")
            : i18n.t("chat.skillUpdated");
        ctx.options.onSkillAdded(
          (data.skill_name as string) || "",
          description,
          (data.files_count as number) || 0,
        );
      }
      commitAcceptedEvent();
      return true;
    }
  }

  // Events that transform message state via processMessageEvent
  const subagentStack = ctx.activeSubagentStackRef.current;

  // Manage subagent stack as side effect
  if (eventType === "agent:call") {
    const agentId = data.agent_id || "unknown";
    subagentStack.push({ agent_id: agentId, depth, message_id: messageId });
  }

  const commitMessageEvent = (
    committedData: EventData = data,
    onApplied: () => void = commitAcceptedEvent,
  ) => ctx.setMessages((prev) => {
    if (binding) {
      if (
        ctx.streamVersionRef.current !== binding.streamVersion ||
        ctx.sessionIdRef.current !== binding.sessionId ||
        ctx.currentRunIdRef.current !== binding.runId
      ) {
        return prev;
      }
    }
    const acceptedCursor = ctx.acceptedStreamCursorRef?.current;
    if (
      acceptedCursor &&
      acceptedCursor.sessionId === progressSessionId &&
      acceptedCursor.runId === progressRunId &&
      acceptedCursor.eventId
    ) {
      const cursorComparison = compareTransportCursors(
        eventId,
        acceptedCursor.eventId,
      );
      if (cursorComparison !== null && cursorComparison <= 0) {
        commitTransportOnly();
        return prev;
      }
    }
    const currentAcceptedProgress = ctx.acceptedRunEventSequenceRef?.current;
    if (
      progressSequence !== null &&
      currentAcceptedProgress &&
      currentAcceptedProgress.sessionId === progressSessionId &&
      currentAcceptedProgress.runId === progressRunId &&
      currentAcceptedProgress.sequence !== null &&
      progressSequence <= currentAcceptedProgress.sequence
    ) {
      commitTransportOnly();
      return prev;
    }
    let didApply = false;
    const next = prev.map((m) => {
      if (m.id !== messageId) return m;

      const result = processMessageEvent(
        eventType,
        committedData,
        m.parts || [],
        m.content,
        m.toolCalls || [],
        depth,
        subagentStack,
        true, // isStreaming
        messageId,
      );
      didApply = true;

      const updated = {
        ...m,
        parts: result.parts,
        content: result.content,
        toolCalls: result.toolCalls,
      };

      if (result.toolResult) {
        updated.toolResults = [...(m.toolResults || []), result.toolResult];
      }
      if (result.tokenUsage) {
        updated.tokenUsage = result.tokenUsage;
      }
      if (result.duration) {
        updated.duration = result.duration;
      }
      if (result.cancelled) {
        updated.isStreaming = false;
        updated.cancelled = true;
      }

      return updated;
    });
    if (didApply) onApplied();
    return next;
  });

  const owner = presentationOwner(binding, messageId);
  const assistantDelta =
    eventType === "message:chunk" &&
    isAssistantTextProjection(data) &&
    data.projection_kind === "assistant_delta";
  const assistantFinal =
    eventType === "message:chunk" &&
    isAssistantTextProjection(data) &&
    data.projection_kind === "assistant_final";
  const executionEventType =
    eventType === "run_event" ? String(data.event_type || "") : eventType;
  const executionPhase =
    executionEventType === "execution_step"
      ? "started"
      : executionEventType === "execution_progress"
        ? "progress"
        : executionEventType === "execution_step_completed" ||
            executionEventType === "execution_step_failed"
          ? "terminal"
          : null;

  if (owner && assistantFinal) {
    ctx.publicStreamPresentation?.flush(owner);
  }
  if (owner && eventType === "error") {
    ctx.publicStreamPresentation?.flush(owner);
  }
  if (owner && assistantDelta && data.content) {
    const presentation = ctx.publicStreamPresentation;
    if (presentation) {
      const ownsPresentation = presentation.owns(owner);
      if (presentation.enqueueAssistantDelta(
        owner,
        data.content,
        (content, onApplied) =>
          commitMessageEvent({ ...data, content }, onApplied),
        {
          onCommitted: commitAcceptedEvent,
          semanticEventId,
          sequence: progressSequence,
        },
      )) {
        return true;
      }
      if (ownsPresentation) return false;
    }
  }
  if (
    owner &&
    executionPhase &&
    isPublicExecutionEvent(executionEventType, data) &&
    typeof data.sequence === "number"
  ) {
    const presentation = ctx.publicStreamPresentation;
    if (presentation) {
      const ownsPresentation = presentation.owns(owner);
      if (presentation.enqueueExecutionUpdate(owner, {
        stepId: data.step_id,
        sequence: data.sequence,
        phase: executionPhase,
        commit: () => commitMessageEvent(),
      })) {
        return true;
      }
      if (ownsPresentation) return false;
    }
  }

  commitMessageEvent();

  // Pop subagent stack after agent:result
  if (eventType === "agent:result") {
    const agentId = data.agent_id || "unknown";
    const stackIndex = subagentStack.findIndex(
      (item) => item.agent_id === agentId && item.message_id === messageId,
    );
    if (stackIndex !== -1) {
      subagentStack.splice(stackIndex, 1);
    }
  }

  // Sandbox side effects
  if (eventType === "sandbox:starting") {
    ctx.setIsInitializingSandbox(true);
    ctx.setSandboxError(null);
  }
  if (eventType === "sandbox:ready") {
    ctx.setIsInitializingSandbox(false);
  }
  if (eventType === "sandbox:error") {
    ctx.setIsInitializingSandbox(false);
    ctx.setSandboxError(
      data.error
        ? translateBackendError(data.error, i18n.t.bind(i18n))
        : i18n.t("chat.sandboxInitFailed"),
    );
  }

  // Error side effects
  if (eventType === "error") {
    dismissQueueToast(ctx);
    ctx.setConnectionStatus("disconnected");
    ctx.setIsInitializingSandbox(false);
    ctx.options?.onClearApprovals?.();
  }
  return true;
}

// ---- Events handled outside processMessageEvent ----

function handleUserMessage(
  data: EventData,
  _messageId: string,
  eventTimestamp: string | undefined,
  ctx: EventHandlerContext,
): void {
  const extractOptimisticContent = (content: string): string | null => {
    const match = content.match(/^\[[^\]]+\]\s([\s\S]*)$/);
    return match ? match[1] : null;
  };
  const resolvedMessageId =
    typeof data.message_id === "string" && data.message_id.trim()
      ? data.message_id
      : typeof data.run_id === "string" && data.run_id.trim()
        ? `${data.run_id}:user`
        : uuid();
  const userContent = data.content || "";
  const userAttachments = convertAttachments(data.attachments) || [];

  if (userContent) {
    ctx.setMessages((prev) => {
      if (prev.length === 0) {
        const newUserMessage: Message = {
          id: resolvedMessageId,
          role: "user",
          content: userContent,
          timestamp: eventTimestamp ? parseDate(eventTimestamp) : new Date(),
          attachments: userAttachments,
        };
        return [...prev, newUserMessage];
      }
      const existingUserMsg = prev.find(
        (m) => m.role === "user" && m.content === userContent,
      );
      if (existingUserMsg) return prev;

      const optimisticContent = extractOptimisticContent(userContent);
      if (optimisticContent) {
        for (let index = prev.length - 1; index >= 0; index -= 1) {
          const candidate = prev[index];
          if (
            candidate?.role === "user" &&
            candidate.content === optimisticContent
          ) {
            const updatedMessages = [...prev];
            updatedMessages[index] = {
              ...candidate,
              content: userContent,
              attachments:
                userAttachments.length > 0
                  ? userAttachments
                  : candidate.attachments,
            };
            return updatedMessages;
          }
        }
      }

      const newUserMessage: Message = {
        id: resolvedMessageId,
        role: "user",
        content: userContent,
        timestamp: eventTimestamp ? parseDate(eventTimestamp) : new Date(),
        attachments: userAttachments,
      };
      const streamingAssistantIndex = prev.findIndex(
        (m) => m.role === "assistant" && m.isStreaming,
      );
      if (streamingAssistantIndex !== -1) {
        const newMessages = [...prev];
        newMessages.splice(streamingAssistantIndex, 0, newUserMessage);
        return newMessages;
      }
      return [...prev, newUserMessage];
    });
  }
}

function handleError(
  data: EventData,
  messageId: string,
  ctx: EventHandlerContext,
  forceCancelled?: boolean,
  options?: { keepConnectionOpen?: boolean },
): void {
  const errorMsg = data.error
    ? translateBackendError(data.error, i18n.t.bind(i18n))
    : i18n.t("chat.unknownError");
  const isCancelled = forceCancelled || data.type === "CancelledError";

  ctx.setMessages((prev) =>
    prev.map((m) => {
      if (m.id !== messageId) return m;
      if (isCancelled) {
        return {
          ...m,
          isStreaming: false,
          cancelled: true,
          parts: appendCancelledPart(
            collapsePublicExecutionSteps(clearAllLoadingStates(m.parts || [])),
          ),
        };
      }
      return {
        ...m,
        content: i18n.t("chat.errorPrefix", { error: errorMsg }),
        isStreaming: false,
        parts: collapsePublicExecutionSteps(
          clearAllLoadingStates(m.parts || []),
        ),
      };
    }),
  );
  if (!options?.keepConnectionOpen) {
    ctx.setConnectionStatus("disconnected");
    ctx.setIsInitializingSandbox(false);
  }
  ctx.options?.onClearApprovals?.();
}

function appendCancelledPart(parts: MessagePart[]): MessagePart[] {
  if (parts.some((part) => part.type === "cancelled")) {
    return parts;
  }
  return [...parts, { type: "cancelled" }];
}
