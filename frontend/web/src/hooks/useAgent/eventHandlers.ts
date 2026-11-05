/**
 * Applies validated public v4 stream events to useAgent state.
 * Wire parsing and connection identity are owned by the SSE adapter/connection.
 */

import { toast } from "@lobehub/ui/base-ui";
import type { Message } from "../../types";
import i18n from "../../i18n";
import type { SubagentStackItem } from "./types";
import {
  processMessageEvent,
  type ProcessMessageEventResult,
} from "./eventProcessor";
import {
  type PublicStreamPresentation,
  type PublicStreamPresentationOwner,
} from "./publicStreamPresentation";
import {
  terminalRunStatusFromEvent,
  type TerminalRunStatus,
} from "./runLifecycle";
import {
  comparePublicRunStreamCursors,
  isV4MessageCorrelatedEventType,
  type V4PublicEvent,
} from "../../components/chat/assistant-ui/publicEventAdapter";
import {
  assistantTextPartIdIsPresentInOtherMessage,
  isAssistantTextPartEventType,
} from "../../types/assistantTextParts";

/**
 * Context passed to event handler
 */
export interface EventHandlerContext {
  sessionIdRef: React.MutableRefObject<string | null>;
  currentRunIdRef: React.MutableRefObject<string | null>;
  processedEventIdsRef: React.MutableRefObject<Set<string>>;
  acceptedRunEventSequenceRef?: React.MutableRefObject<AcceptedRunEventSequence>;
  acceptedStreamCursorRef?: React.MutableRefObject<AcceptedStreamCursor>;
  v4TerminalReservationsRef?: React.MutableRefObject<Set<string>>;
  v4TerminalFenceRef?: React.MutableRefObject<V4TerminalFence | null>;
  v4MessageOwnerRef?: React.MutableRefObject<V4MessageOwner | null>;
  v4MessageCandidateRef?: React.MutableRefObject<V4MessageCandidate | null>;
  lastHistoryTimestampRef: React.MutableRefObject<Date | null>;
  activeSubagentStackRef: React.MutableRefObject<SubagentStackItem[]>;
  streamVersionRef: React.MutableRefObject<number>;
  setSessionId: (id: string) => void;
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  /** Immediate stream projection snapshot; React state is only its render projection. */
  messagesRef: React.MutableRefObject<Message[]>;
  setConnectionStatus: (status: string) => void;
  setIsInitializingSandbox: (loading: boolean) => void;
  setSandboxError: (error: string | null) => void;
  onRunTerminal?: (
    runId: string,
    status: TerminalRunStatus,
    messageId: string,
    onSettled?: (accepted: boolean) => boolean,
  ) => boolean;
  onRunStatusUnavailable?: (runId: string, messageId: string) => boolean;
  dismissQueueToast?: () => void;
  publicStreamPresentation?: PublicStreamPresentation;
}

export interface V4MessageOwner {
  sessionId: string;
  runId: string;
  streamVersion: number;
  streamIncarnation: number;
  protocolMessageId: string;
  reducerMessageId: string;
}

export interface V4MessageCandidate {
  sessionId: string;
  runId: string;
  streamVersion: number;
  streamIncarnation: number;
  protocolMessageId: string;
}

export function rebindV4MessageOwner(
  ownerRef: React.MutableRefObject<V4MessageOwner | null>,
  binding: Pick<
    V4MessageOwner,
    "sessionId" | "runId" | "streamVersion" | "streamIncarnation"
  >,
  reducerMessageId: string,
  protocolMessageId?: string,
): boolean {
  const owner = ownerRef.current;
  if (protocolMessageId) {
    const ownerMatchesBinding = Boolean(
      owner &&
        owner.sessionId === binding.sessionId &&
        owner.runId === binding.runId &&
        owner.streamVersion === binding.streamVersion &&
        owner.streamIncarnation === binding.streamIncarnation,
    );
    if (ownerMatchesBinding && owner!.protocolMessageId !== protocolMessageId) {
      return false;
    }
    ownerRef.current = {
      sessionId: binding.sessionId,
      runId: binding.runId,
      streamVersion: binding.streamVersion,
      streamIncarnation: binding.streamIncarnation,
      protocolMessageId,
      reducerMessageId,
    };
    return true;
  }
  if (
    !owner ||
    owner.sessionId !== binding.sessionId ||
    owner.runId !== binding.runId ||
    owner.streamVersion !== binding.streamVersion ||
    owner.streamIncarnation !== binding.streamIncarnation
  ) {
    return false;
  }
  owner.reducerMessageId = reducerMessageId;
  return true;
}

export interface V4TerminalFence {
  sessionId: string;
  runId: string;
  streamIncarnation: number;
  generation?: number;
  streamVersion?: number;
  terminalEventId: string;
  streamEndEventId?: string;
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
      fence.runId === event.runId &&
      fence.streamIncarnation === event.streamIncarnation &&
      fence.generation === event.generation,
  );
}

function isCurrentV4TerminalSettle(
  event: V4PublicEvent,
  ctx: EventHandlerContext,
  streamVersion: number,
  sessionId: string | null,
): boolean {
  const acceptedCursor = ctx.acceptedStreamCursorRef?.current;
  return Boolean(
    sessionId &&
      ctx.sessionIdRef.current === sessionId &&
      ctx.currentRunIdRef.current === event.runId &&
      ctx.streamVersionRef.current === streamVersion &&
      !(
        acceptedCursor?.sessionId === sessionId &&
        acceptedCursor.runId === event.runId &&
        acceptedCursor.streamIncarnation != null &&
        acceptedCursor.streamIncarnation !== event.streamIncarnation
      ),
  );
}

export function acceptV4TerminalFence(
  event: V4PublicEvent,
  ctx: EventHandlerContext,
  terminalEventId: string,
  streamVersion: number,
  onAccepted: () => void,
): boolean {
  const sessionId = ctx.sessionIdRef.current;
  if (!isCurrentV4TerminalSettle(event, ctx, streamVersion, sessionId)) {
    return false;
  }
  ctx.v4TerminalFenceRef!.current = {
    sessionId: sessionId!,
    runId: event.runId,
    streamIncarnation: event.streamIncarnation,
    generation: event.generation,
    streamVersion,
    terminalEventId,
  };
  onAccepted();
  return true;
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
  streamIncarnation?: number;
  generation?: number;
}

/**
 * v4 frames are accepted only from the exact current connection owner. The
 * stream incarnation and generation are deliberately required here; an
 * omitted value must never act as a wildcard for terminal or end frames.
 */
export interface V4StreamEventBinding extends StreamEventBinding {
  streamIncarnation: number;
  generation: number;
}

function isStrictV4Binding(
  binding: StreamEventBinding | undefined,
): binding is V4StreamEventBinding {
  return Boolean(
    binding &&
      binding.sessionId &&
      binding.runId &&
      Number.isSafeInteger(binding.streamVersion) &&
      binding.streamVersion >= 0 &&
      typeof binding.streamIncarnation === "number" &&
      Number.isSafeInteger(binding.streamIncarnation) &&
      binding.streamIncarnation >= 1 &&
      typeof binding.generation === "number" &&
      Number.isSafeInteger(binding.generation) &&
      binding.generation >= 0,
  );
}

function isCurrentV4GapOwner(
  ctx: EventHandlerContext,
  binding: V4StreamEventBinding,
  event: V4PublicEvent,
): boolean {
  if (
    ctx.sessionIdRef.current !== binding.sessionId ||
    ctx.currentRunIdRef.current !== binding.runId ||
    ctx.streamVersionRef.current !== binding.streamVersion
  ) {
    return false;
  }
  const acceptedCursor = ctx.acceptedStreamCursorRef?.current;
  if (!acceptedCursor) return false;
  if (
    acceptedCursor.sessionId === binding.sessionId &&
    acceptedCursor.runId === binding.runId &&
    acceptedCursor.streamIncarnation === binding.streamIncarnation
  ) {
    const payload = event.event.payload as Record<string, unknown>;
    const requestedEventId = payload.requested_event_id;
    const requestedIncarnation = payload.requested_stream_incarnation;
    return (
      requestedEventId !== null &&
      requestedIncarnation === binding.streamIncarnation &&
      acceptedCursor.eventId ===
        `${binding.runId}:${requestedIncarnation}:${requestedEventId}`
    );
  }
  const cursorIsUnbound =
    acceptedCursor.eventId === null &&
    acceptedCursor.streamIncarnation == null &&
    ((acceptedCursor.sessionId === null && acceptedCursor.runId === null) ||
      (acceptedCursor.sessionId === binding.sessionId &&
        acceptedCursor.runId === binding.runId));
  return (
    cursorIsUnbound &&
    (event.event.payload as Record<string, unknown>).requested_event_id === null &&
    (event.event.payload as Record<string, unknown>)
      .requested_stream_incarnation === null
  );
}

function canAcceptV4TerminalSequence(
  event: V4PublicEvent,
  ctx: EventHandlerContext,
  binding: V4StreamEventBinding,
): boolean {
  if (event.sequence === null) return true;
  const accepted = ctx.acceptedRunEventSequenceRef?.current;
  if (!accepted) return false;
  return (
    ctx.sessionIdRef.current === binding.sessionId &&
    ctx.currentRunIdRef.current === binding.runId &&
    ctx.streamVersionRef.current === binding.streamVersion &&
    !(
      accepted.sessionId === binding.sessionId &&
      accepted.runId === binding.runId &&
      accepted.sequence !== null &&
      event.sequence <= accepted.sequence
    )
  );
}

function commitV4TerminalSequence(
  event: V4PublicEvent,
  ctx: EventHandlerContext,
  binding: V4StreamEventBinding,
): boolean {
  if (!canAcceptV4TerminalSequence(event, ctx, binding)) return false;
  if (event.sequence !== null && ctx.acceptedRunEventSequenceRef) {
    ctx.acceptedRunEventSequenceRef.current = {
      sessionId: binding.sessionId,
      runId: binding.runId,
      sequence: event.sequence,
    };
  }
  return true;
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

/**
 * Publish a message projection without putting stream bookkeeping in a React
 * updater. The ref is the receive-time snapshot used by reconnect recovery;
 * React receives only the already-derived value.
 */
export function setMessageSnapshot(
  ctx: Pick<EventHandlerContext, "messagesRef" | "setMessages">,
  update: React.SetStateAction<Message[]>,
): void {
  const current = ctx.messagesRef.current;
  const next = typeof update === "function" ? update(current) : update;
  ctx.messagesRef.current = next;
  ctx.setMessages(next);
}

/** Apply one frame after its adapter/connection binding has been admitted. */
function applyPublicRunStreamEventV4(
  event: V4PublicEvent,
  messageId: string,
  ctx: EventHandlerContext,
  binding: V4StreamEventBinding,
  onCommitted?: (semanticApplied: boolean) => void,
  onTerminalSettled?: (accepted: boolean) => void,
): boolean {
  const terminalEventId =
    event.eventType === "stream.end" || event.eventType.startsWith("run.")
      ? ((event.event.payload as unknown as Record<string, unknown>).terminal_event_id as string | undefined)
      : undefined;
  const terminalEndFenced =
    event.eventType === "stream.end" &&
    matchesV4TerminalFence(event, ctx, terminalEventId) &&
    ctx.v4TerminalFenceRef?.current?.streamVersion === binding.streamVersion;
  if (
    ctx.sessionIdRef.current !== binding.sessionId ||
    (!terminalEndFenced &&
      (event.runId !== ctx.currentRunIdRef.current ||
        ctx.streamVersionRef.current !== binding.streamVersion))
  ) {
    return false;
  }
  const terminalStatus = terminalRunStatusFromEvent(
    event.eventType,
    event.event as unknown as Record<string, unknown>,
  );
  if (terminalStatus && event.eventType !== "stream.end") {
    if (
      !terminalEventId ||
      !ctx.onRunTerminal ||
      !ctx.v4TerminalFenceRef ||
      ctx.v4TerminalFenceRef?.current?.terminalEventId === terminalEventId ||
      ctx.v4TerminalReservationsRef?.current.has(terminalEventId) ||
      !canAcceptV4TerminalSequence(event, ctx, binding)
    ) {
      if (
        terminalEventId &&
        ctx.v4TerminalFenceRef?.current?.terminalEventId === terminalEventId
      ) {
        onCommitted?.(false);
      }
      return false;
    }
    ctx.v4TerminalReservationsRef?.current.add(terminalEventId);
    const owner = presentationOwner(binding, messageId);
    if (owner) ctx.publicStreamPresentation?.flush(owner);
    const settle = (accepted: boolean): boolean => {
      const sessionId = ctx.sessionIdRef.current;
      ctx.v4TerminalReservationsRef?.current.delete(terminalEventId);
      if (
        !accepted ||
        !isCurrentV4TerminalSettle(
          event,
          ctx,
          binding.streamVersion,
          sessionId,
        ) ||
        !commitV4TerminalSequence(event, ctx, binding)
      ) {
        onTerminalSettled?.(false);
        return false;
      }
      const fenceAccepted = acceptV4TerminalFence(
        event,
        ctx,
        terminalEventId,
        binding.streamVersion,
        () => {
          onCommitted?.(false);
        },
      );
      onTerminalSettled?.(fenceAccepted);
      return fenceAccepted;
    };
    const accepted = ctx.onRunTerminal(
      event.runId,
      terminalStatus,
      messageId,
      settle,
    );
    if (!accepted) {
      ctx.v4TerminalReservationsRef?.current.delete(terminalEventId);
      return false;
    }
    return true;
  }
  if (event.eventType === "stream.end") {
    const fenced = matchesV4TerminalFence(event, ctx, terminalEventId);
    if (!fenced) return false;
    if (fenced && ctx.v4TerminalFenceRef?.current?.streamEndEventId) {
      onCommitted?.(false);
      return false;
    }
    if (fenced && ctx.v4TerminalFenceRef) {
      ctx.v4TerminalFenceRef.current!.streamEndEventId = event.eventId;
    }
    onCommitted?.(false);
    return true;
  }
  if (ctx.processedEventIdsRef.current.has(event.semanticKey)) {
    // History reconstruction already applied this event. Replaying its
    // message.started frame must still restore the protocol-to-UI owner before
    // later message-correlated frames arrive.
    if (
      event.eventType === "message.started" &&
      event.messageId &&
      ctx.v4MessageOwnerRef
    ) {
      const candidate = ctx.v4MessageCandidateRef?.current;
      const candidateConflicts = Boolean(
        candidate &&
          candidate.sessionId === binding.sessionId &&
          candidate.runId === binding.runId &&
          candidate.streamVersion === binding.streamVersion &&
          candidate.streamIncarnation === binding.streamIncarnation &&
          candidate.protocolMessageId !== event.messageId,
      );
      const rebound =
        !candidateConflicts &&
        rebindV4MessageOwner(
          ctx.v4MessageOwnerRef,
          binding,
          messageId,
          event.messageId,
        );
      if (rebound && ctx.v4MessageCandidateRef) {
        ctx.v4MessageCandidateRef.current = null;
      }
    }
    onCommitted?.(false);
    return false;
  }
  const owner = ctx.v4MessageOwnerRef?.current;
  const ownerMatchesRun = Boolean(
    owner &&
      owner.sessionId === binding.sessionId &&
      owner.runId === binding.runId &&
      owner.streamVersion === binding.streamVersion &&
      owner.streamIncarnation === binding.streamIncarnation,
  );
  const candidate = ctx.v4MessageCandidateRef?.current;
  const candidateMatchesRun = Boolean(
    candidate &&
      candidate.sessionId === binding.sessionId &&
      candidate.runId === binding.runId &&
      candidate.streamVersion === binding.streamVersion &&
      candidate.streamIncarnation === binding.streamIncarnation,
  );
  const messageCorrelatedEvent = isV4MessageCorrelatedEventType(
    event.eventType,
  );
  const isMessageLifecycleEvent = event.eventType.startsWith("message.");
  const isPreOwnerActivity =
    messageCorrelatedEvent && !isMessageLifecycleEvent;
  let projectedMessageId = messageId;
  if (event.messageId && messageCorrelatedEvent) {
    if (ownerMatchesRun) {
      if (owner!.protocolMessageId !== event.messageId) return false;
      projectedMessageId = owner!.reducerMessageId;
    } else if (event.eventType !== "message.started" && !isPreOwnerActivity) {
      // Message lifecycle events cannot invent an owner before message.started.
      return false;
    } else if (
      candidateMatchesRun &&
      candidate!.protocolMessageId !== event.messageId
    ) {
      // Correlated activity may precede message.started, but it must remain
      // attached to one protocol message identity until the owner is declared.
      return false;
    }
  } else if (ownerMatchesRun && messageCorrelatedEvent) {
    projectedMessageId = owner!.reducerMessageId;
  }
  let precomputedTextMutation: ProcessMessageEventResult | null = null;
  if (
    event.eventType === "message.delta" ||
    isAssistantTextPartEventType(event.eventType)
  ) {
    const targetMessage = ctx.messagesRef.current.find(
      (message) => message.id === projectedMessageId,
    );
    if (
      !targetMessage ||
      targetMessage.role !== "assistant" ||
      (isAssistantTextPartEventType(event.eventType) &&
        targetMessage.runId !== binding.runId)
    ) {
      return false;
    }
    if (isAssistantTextPartEventType(event.eventType)) {
      const payload = event.event.payload as unknown as Record<string, unknown>;
      const partId = payload.part_id;
      if (
        typeof partId !== "string" ||
        assistantTextPartIdIsPresentInOtherMessage(
          ctx.messagesRef.current,
          projectedMessageId,
          partId,
        )
      ) {
        return false;
      }
    }
    precomputedTextMutation = processMessageEvent(
      event,
      undefined,
      targetMessage.parts || [],
      targetMessage.content,
      targetMessage.toolCalls || [],
      0,
      ctx.activeSubagentStackRef.current,
      true,
      projectedMessageId,
    );
    if (precomputedTextMutation.accepted === false) return false;
  }
  const acceptedCursor = ctx.acceptedStreamCursorRef?.current;
  if (
    acceptedCursor?.sessionId === binding.sessionId &&
    acceptedCursor.runId === binding.runId &&
    acceptedCursor.eventId
  ) {
    const comparison = compareTransportCursors(
      event.transportCursor,
      acceptedCursor.eventId,
    );
    if (comparison !== null && comparison <= 0) {
      onCommitted?.(false);
      return false;
    }
  }
  const acceptedSequence = ctx.acceptedRunEventSequenceRef?.current;
  if (
    event.sequence !== null &&
    acceptedSequence?.sessionId === binding.sessionId &&
    acceptedSequence.runId === binding.runId &&
    acceptedSequence.sequence !== null &&
    (event.sequence < acceptedSequence.sequence ||
      (event.sequence === acceptedSequence.sequence && ownerMatchesRun))
  ) {
    if (!ctx.messagesRef.current.some(
      (message) => message.id === projectedMessageId &&
        message.role === "assistant" && message.runId === binding.runId,
    )) return false;
    if (event.sequence < acceptedSequence.sequence) {
      // History can be ahead of initial Redis replay without projecting
      // message.started. Restore its owner, but never replay older content.
      if (
        event.eventType === "message.started" &&
        event.messageId &&
        ctx.v4MessageOwnerRef
      ) {
        if (!rebindV4MessageOwner(
          ctx.v4MessageOwnerRef,
          binding,
          messageId,
          event.messageId,
        )) return false;
        if (ctx.v4MessageCandidateRef) ctx.v4MessageCandidateRef.current = null;
      }
    }
    onCommitted?.(false);
    return false;
  }
  const commitV4Event = () => {
    ctx.processedEventIdsRef.current.add(event.semanticKey);
    if (ctx.processedEventIdsRef.current.size > 10_000) {
      ctx.processedEventIdsRef.current.clear();
      ctx.processedEventIdsRef.current.add(event.semanticKey);
    }
    if (event.sequence !== null && ctx.acceptedRunEventSequenceRef) {
      ctx.acceptedRunEventSequenceRef.current = {
        sessionId: binding.sessionId,
        runId: binding.runId,
        sequence: event.sequence,
      };
    }
    onCommitted?.(true);
  };
  if (event.eventType === "stream.open") {
    if (ctx.dismissQueueToast) {
      ctx.dismissQueueToast();
    } else {
      toast.dismiss("chat-queue");
        toast.success({ description: i18n.t("chat.queueStart"), duration: 2000, hideCloseButton: true });
    }
    commitV4Event();
    return true;
  }
  if (event.eventType === "stream.heartbeat") {
    commitV4Event();
    return true;
  }
  const subagentStack = ctx.activeSubagentStackRef.current;
  const presentation = presentationOwner(binding, projectedMessageId);
  if (presentation) ctx.publicStreamPresentation?.flush(presentation);
  let didApply = false;
  const next = ctx.messagesRef.current.map((message) => {
    if (message.id !== projectedMessageId) return message;
    const result = precomputedTextMutation || processMessageEvent(
      event,
      undefined,
      message.parts || [],
      message.content,
      message.toolCalls || [],
      0,
      subagentStack,
      true,
      projectedMessageId,
    );
    if (result.accepted === false) return message;
    didApply = true;
    const updated: Message = {
      ...message,
      parts: result.parts,
      content: result.content,
      toolCalls: result.toolCalls,
    };
    if (result.toolResult) {
      updated.toolResults = [...(message.toolResults || []), result.toolResult];
    }
    if (result.tokenUsage) updated.tokenUsage = result.tokenUsage;
    if (result.duration !== undefined) updated.duration = result.duration;
    if (result.cancelled) {
      updated.isStreaming = false;
      updated.cancelled = true;
    }
    return updated;
  });
  if (!didApply) return false;
  ctx.messagesRef.current = next;
  ctx.setMessages(next);
  commitV4Event();
  if (
    event.messageId &&
    isPreOwnerActivity &&
    !ownerMatchesRun &&
    ctx.v4MessageCandidateRef
  ) {
    ctx.v4MessageCandidateRef.current = {
      sessionId: binding.sessionId,
      runId: binding.runId,
      streamVersion: binding.streamVersion,
      streamIncarnation: binding.streamIncarnation,
      protocolMessageId: event.messageId,
    };
  }
  if (
    event.eventType === "message.started" &&
    event.messageId &&
    ctx.v4MessageOwnerRef
  ) {
    rebindV4MessageOwner(
      ctx.v4MessageOwnerRef,
      binding,
      messageId,
      event.messageId,
    );
    if (ctx.v4MessageCandidateRef) ctx.v4MessageCandidateRef.current = null;
  }
  return true;
}

export type V4FrameHandlingResult =
  | { kind: "invalid" }
  | { kind: "duplicate" }
  | { kind: "applied" }
  | { kind: "deferred"; reason: "terminal_hydration" };

export interface PublicRunStreamEventV4Args {
  event: V4PublicEvent;
  messageId: string;
  ctx: EventHandlerContext;
  binding?: StreamEventBinding;
  currentGeneration: number;
  onGap?: (event: V4PublicEvent) => void;
  onCommitted?: (semanticApplied: boolean) => void;
  onTerminalSettled?: (accepted: boolean) => void;
}

/** Admit one adapter event against its connection, then apply it. */
export function handlePublicRunStreamEventV4Result(
  args: PublicRunStreamEventV4Args,
): V4FrameHandlingResult {
  const binding = args.binding;
  if (
    !isStrictV4Binding(binding) ||
    !Number.isSafeInteger(args.currentGeneration) ||
    args.currentGeneration < 0 ||
    args.currentGeneration !== binding.generation ||
    args.event.runId !== binding.runId ||
    args.event.generation !== binding.generation ||
    (args.event.eventType !== "stream.gap" &&
      args.event.streamIncarnation !== binding.streamIncarnation)
  ) {
    return { kind: "invalid" };
  }
  const event = args.event;
  if (event.eventType === "stream.gap") {
    // A gap advertises the replacement incarnation while binding the
    // connection to the requested cursor's prior incarnation.
    if (!isCurrentV4GapOwner(args.ctx, binding, event)) {
      return { kind: "invalid" };
    }
    args.onGap?.(event);
    return { kind: "invalid" };
  }
  let committed: boolean | undefined;
  let terminalSettled: boolean | undefined;
  const accepted = applyPublicRunStreamEventV4(
    event,
    args.messageId,
    args.ctx,
    binding,
    (semanticApplied) => {
      committed = semanticApplied;
      args.onCommitted?.(semanticApplied);
    },
    (settled) => {
      terminalSettled = settled;
      args.onTerminalSettled?.(settled);
    },
  );
  if (!accepted) {
    if (committed === false) return { kind: "duplicate" };
    if (committed === true) return { kind: "applied" };
    return { kind: "invalid" };
  }
  if (
    (event.eventType === "run.succeeded" ||
      event.eventType === "run.failed" ||
      event.eventType === "run.cancelled") &&
    terminalSettled === undefined
  ) {
    return { kind: "deferred", reason: "terminal_hydration" };
  }
  return { kind: "applied" };
}
