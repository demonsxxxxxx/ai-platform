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
import { clearAllLoadingStates } from "./messageParts";
import { convertAttachments, processMessageEvent } from "./eventProcessor";
import {
  terminalRunStatusFromEvent,
  type TerminalRunStatus,
} from "./runLifecycle";

/**
 * Context passed to event handler
 */
export interface EventHandlerContext {
  options?: UseAgentOptions;
  sessionIdRef: React.MutableRefObject<string | null>;
  currentRunIdRef: React.MutableRefObject<string | null>;
  processedEventIdsRef: React.MutableRefObject<Set<string>>;
  acceptedRunEventSequenceRef?: React.MutableRefObject<AcceptedRunEventSequence>;
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
  ) => boolean;
  onRunStatusUnavailable?: (runId: string, messageId: string) => boolean;
  dismissQueueToast?: () => void;
}

/** Durable, bounded progress cursor for the active session/run stream. */
export interface AcceptedRunEventSequence {
  sessionId: string | null;
  runId: string | null;
  sequence: number | null;
}

/** The connection generation that authorizes a runless stream frame. */
export interface StreamEventBinding {
  sessionId: string;
  runId: string;
  streamVersion: number;
}

const MESSAGE_EVENTS = new Set<string>([
  "agent:call",
  "agent:result",
  "thinking",
  "message:chunk",
  "final_detail",
  "tool:start",
  "tool:result",
  "sandbox:starting",
  "sandbox:ready",
  "sandbox:error",
  "token:usage",
  "todo:updated",
  "summary",
  "run_event",
  "artifact_card",
  "error",
]);

const SIDE_EFFECT_EVENTS = new Set<string>([
  "metadata",
  "user:message",
  "user:cancel",
  "complete",
  "done",
  "queue_update",
  "approval_required",
  "skills:changed",
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
 * Handle incoming SSE events
 */
export function handleStreamEvent(
  event: StreamEvent,
  messageId: string,
  eventId: string,
  eventTimestamp: string | undefined,
  ctx: EventHandlerContext,
  binding?: StreamEventBinding,
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

  const terminalStatus = terminalRunStatusFromEvent(
    eventType,
    data as unknown as Record<string, unknown>,
  );
  // Only a generation-bound SSE connection may bind a runless terminal frame.
  // Generic event handling, including history replay, must have an explicit id.
  if (terminalStatus && !eventRunId) {
    return false;
  }

  if (!SIDE_EFFECT_EVENTS.has(eventType) && !MESSAGE_EVENTS.has(eventType)) {
    console.warn("[SSE] Unhandled event type:", eventType);
    return false;
  }

  if (ctx.processedEventIdsRef.current.has(eventId)) {
    console.log("[SSE] Skipping duplicate event by ID:", eventId);
    return false;
  }

  if (eventTimestamp && ctx.lastHistoryTimestampRef.current) {
    const eventTime = parseDate(eventTimestamp);
    const historyTime = ctx.lastHistoryTimestampRef.current;
    if (eventTime <= historyTime) {
      console.log(
        "[SSE] Skipping duplicate event by timestamp:",
        eventId,
        eventTime.toISOString(),
        "<=",
        historyTime.toISOString(),
      );
      return false;
    }
  }

  const progressSequence = runEventSequence(data);
  const progressSessionId = binding?.sessionId ?? ctx.sessionIdRef.current;
  const progressRunId = binding?.runId ?? eventRunId;
  const acceptedProgress = ctx.acceptedRunEventSequenceRef?.current;
  // Sequenced run_event frames can only be accepted through the durable
  // cursor. A partial caller without that cursor must fail closed instead of
  // accidentally restoring the reconnect budget.
  if (eventType === "run_event" && progressSequence !== null && !acceptedProgress) {
    return false;
  }
  if (
    eventType === "run_event" &&
    progressSequence !== null &&
    progressSessionId &&
    progressRunId &&
    acceptedProgress &&
    acceptedProgress.sessionId === progressSessionId &&
    acceptedProgress.runId === progressRunId &&
    acceptedProgress.sequence !== null &&
    progressSequence <= acceptedProgress.sequence
  ) {
    return false;
  }

  // Mark only after all current-run/generation, parse, replay, and timestamp
  // checks have passed. The capped UI dedup set stays bounded; the sequence
  // cursor below remains the replay-safe authority for reconnect recovery.
  ctx.processedEventIdsRef.current.add(eventId);
  if (ctx.processedEventIdsRef.current.size > 10_000) {
    ctx.processedEventIdsRef.current.clear();
    ctx.processedEventIdsRef.current.add(eventId);
  }
  if (
    eventType === "run_event" &&
    progressSequence !== null &&
    progressSessionId &&
    progressRunId
  ) {
    if (ctx.acceptedRunEventSequenceRef) {
      ctx.acceptedRunEventSequenceRef.current = {
        sessionId: progressSessionId,
        runId: progressRunId,
        sequence: progressSequence,
      };
    }
  }

  if (
    terminalStatus &&
    eventRunId &&
    ctx.onRunTerminal?.(eventRunId, terminalStatus, messageId)
  ) {
    return true;
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
      return true;
    }

    case "user:message": {
      handleUserMessage(data, messageId, eventTimestamp, ctx);
      return true;
    }

    case "user:cancel": {
      dismissQueueToast(ctx);
      handleError(data, messageId, ctx, true, { keepConnectionOpen: true });
      return true;
    }

    case "complete":
    case "done": {
      dismissQueueToast(ctx);
      ctx.setMessages((prev) =>
        prev.map((m) =>
          m.id === messageId
            ? {
                ...m,
                isStreaming: false,
                parts: clearAllLoadingStates(m.parts || []),
              }
            : m,
        ),
      );
      ctx.setConnectionStatus("disconnected");
      // AI 回复完成，用户正在查看当前 session，立即标记为已读
      const activeSessionId = ctx.sessionIdRef.current;
      if (activeSessionId) {
        sessionApi.markRead(activeSessionId).catch(() => {});
      }
      ctx.options?.onStreamDone?.();
      return true;
    }

    case "queue_update": {
      if (data.status === "processing") {
        import("react-hot-toast").then(({ default: toast }) => {
          toast.dismiss("chat-queue");
          toast.success(i18n.t("chat.queueStart"), { duration: 2000 });
        });
      }
      return true;
    }

    case "approval_required": {
      handleApprovalRequired(data, ctx);
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

  ctx.setMessages((prev) =>
    prev.map((m) => {
      if (m.id !== messageId) return m;

      const result = processMessageEvent(
        eventType,
        data,
        m.parts || [],
        m.content,
        m.toolCalls || [],
        depth,
        subagentStack,
        true, // isStreaming
        messageId,
      );

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
    }),
  );

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
          parts: appendCancelledPart(clearAllLoadingStates(m.parts || [])),
        };
      }
      return {
        ...m,
        content: i18n.t("chat.errorPrefix", { error: errorMsg }),
        isStreaming: false,
        parts: clearAllLoadingStates(m.parts || []),
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

function handleApprovalRequired(
  data: EventData,
  ctx: EventHandlerContext,
): void {
  void data;
  void ctx;
}
