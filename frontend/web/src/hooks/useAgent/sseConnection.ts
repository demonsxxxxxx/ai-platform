/**
 * SSE Connection utilities for useAgent hook
 * Handles SSE connection, reconnection, and stream management
 */

import { fetchEventSource } from "@microsoft/fetch-event-source";
import { uuid } from "../../utils/uuid";
import { sessionApi } from "../../services/api";
import {
  getValidAccessToken,
  refreshAccessToken,
} from "../../services/api/tokenManager";
import { getRefreshToken } from "../../services/api/token";
import type { EventType, StreamEvent } from "./types";
import { handleStreamEvent, type EventHandlerContext } from "./eventHandlers";
import { clearAllLoadingStates } from "./messageParts";
import {
  isActiveRunStatus,
  terminalRunStatus,
  terminalRunStatusFromEvent,
} from "./runLifecycle";

/**
 * SSE Connection context
 */
export interface SSEConnectionContext extends EventHandlerContext {
  abortControllerRef: React.MutableRefObject<AbortController | null>;
  isConnectingRef: React.MutableRefObject<boolean>;
  streamingMessageIdRef: React.MutableRefObject<string | null>;
  reconnectTimeoutRef: React.MutableRefObject<ReturnType<
    typeof setTimeout
  > | null>;
  retryCountRef: React.MutableRefObject<number>;
  messagesRef: React.MutableRefObject<Message[]>;
}

/**
 * Exponential backoff for reconnection
 */
export function getReconnectDelay(retryCount: number): number {
  const baseDelay = Math.min(Math.pow(2, retryCount), 30) * 1000;
  const jitter = Math.random() * 1000;
  return baseDelay + jitter;
}

/**
 * Clear reconnect timeout
 */
export function clearReconnectTimeout(
  reconnectTimeoutRef: React.MutableRefObject<ReturnType<
    typeof setTimeout
  > | null>,
): void {
  if (reconnectTimeoutRef.current) {
    clearTimeout(reconnectTimeoutRef.current);
    reconnectTimeoutRef.current = null;
  }
}

export type SSECloseAction = "terminal" | "retry";
export type SSEFetchEventSource = typeof fetchEventSource;
type ReconnectDependencies = {
  getStatus?: typeof sessionApi.getStatus;
  connect?: typeof connectToSSE;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isCurrentSSETarget(
  ctx: SSEConnectionContext,
  targetSessionId: string,
  targetRunId: string,
  streamVersion?: number,
): boolean {
  return (
    ctx.sessionIdRef.current === targetSessionId &&
    ctx.currentRunIdRef.current === targetRunId &&
    (streamVersion === undefined || ctx.streamVersionRef.current === streamVersion)
  );
}

function isTerminalErrorPayload(data: unknown): boolean {
  if (!isRecord(data)) {
    return false;
  }

  return (
    typeof data.type === "string" ||
    typeof data.run_id === "string" ||
    typeof data.trace_id === "string"
  );
}

export function isTerminalSSEEvent(eventType: string, data?: unknown): boolean {
  if (isRecord(data) && terminalRunStatusFromEvent(eventType, data)) {
    return true;
  }
  if (eventType === "done" || eventType === "complete") {
    return true;
  }

  if (eventType === "error") {
    return isTerminalErrorPayload(data);
  }

  return false;
}

export function getSSECloseAction({
  receivedTerminalEvent,
}: {
  receivedTerminalEvent: boolean;
}): SSECloseAction {
  return receivedTerminalEvent ? "terminal" : "retry";
}

/**
 * Connect to SSE stream
 */
export async function connectToSSE(
  targetSessionId: string,
  targetRunId: string,
  messageId: string,
  ctx: SSEConnectionContext,
  hasRetried = false,
  fetchStream: SSEFetchEventSource = fetchEventSource,
): Promise<void> {
  const {
    abortControllerRef,
    isConnectingRef,
    streamingMessageIdRef,
    setConnectionStatus,
    retryCountRef,
    streamVersionRef,
  } = ctx;

  // Never let a deferred connection for an old session/run abort the active
  // stream. The target check also gives run-less terminal SSE frames a stream
  // generation boundary before they reach the event handler.
  if (!isCurrentSSETarget(ctx, targetSessionId, targetRunId)) {
    return;
  }

  if (isConnectingRef.current) {
    console.log("[SSE] Connection already in progress, skipping...");
    return;
  }
  isConnectingRef.current = true;
  streamingMessageIdRef.current = messageId;

  if (abortControllerRef.current) {
    abortControllerRef.current.abort();
  }
  const streamAbortController = new AbortController();
  abortControllerRef.current = streamAbortController;
  const streamVersion = streamVersionRef.current;
  const isCurrentStream = () =>
    abortControllerRef.current === streamAbortController &&
    isCurrentSSETarget(ctx, targetSessionId, targetRunId, streamVersion);

  const token = await getValidAccessToken();
  if (!isCurrentStream()) {
    return;
  }
  const headers: Record<string, string> = {};
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  console.log(
    `[SSE] Connecting: session=${targetSessionId}, run_id=${targetRunId}`,
  );

  let receivedTerminalEvent = false;

  setConnectionStatus("connecting");
  retryCountRef.current = 0;

  try {
    await fetchStream(
      `/api/chat/sessions/${targetSessionId}/stream?run_id=${targetRunId}`,
      {
        credentials: "include",
        headers,
        signal: streamAbortController.signal,
        openWhenHidden: true,
        onopen: async (response) => {
          if (!isCurrentStream()) {
            return;
          }
          if (response.status === 401) {
            if (hasRetried) {
              // refreshAccessToken() in the first attempt already handled redirect
              // if needed, so just abort and throw
              throw new Error("SSE unauthorized after token refresh");
            }
            if (!getRefreshToken()) {
              throw new Error("SSE unauthorized: no refresh token");
            }
            try {
              await refreshAccessToken();
            } catch {
              throw new Error("SSE unauthorized: token refresh failed");
            }
            abortControllerRef.current?.abort();
            isConnectingRef.current = false;
            await connectToSSE(
              targetSessionId,
              targetRunId,
              messageId,
              ctx,
              true,
              fetchStream,
            );
            return;
          }
          if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
          }
          console.log("[SSE] Connection established");
          setConnectionStatus("connected");
          retryCountRef.current = 0;
        },
        onmessage: (event) => {
          if (!isCurrentStream()) {
            return;
          }
          if (event.event === "ping") return;
          const eventId = event.id || uuid();
          let parsedData: Record<string, unknown>;
          try {
            parsedData = JSON.parse(event.data);
          } catch {
            // Ignore parse errors
            return;
          }
          if (
            event.event === "error" &&
            !isTerminalSSEEvent(event.event, parsedData)
          ) {
            setConnectionStatus("reconnecting");
            throw new Error("SSE transport error before terminal event");
          }
          if (isTerminalSSEEvent(event.event, parsedData)) {
            receivedTerminalEvent = true;
          }
          const timestamp = parsedData._timestamp as string | undefined;
          const streamEvent: StreamEvent = {
            event: event.event as EventType,
            data: event.data,
          };
          handleStreamEvent(streamEvent, messageId, eventId, timestamp, ctx);
        },
        onerror: (err) => {
          if (!isCurrentStream()) {
            return;
          }
          console.error("[SSE] Connection error:", err);
          setConnectionStatus("reconnecting");
        },
        onclose: () => {
          if (!isCurrentStream()) {
            return;
          }
          console.log("[SSE] Connection closed");
          const closeAction = getSSECloseAction({ receivedTerminalEvent });
          if (closeAction === "retry") {
            setConnectionStatus("reconnecting");
            throw new Error("SSE closed before terminal event");
          }
          setConnectionStatus("disconnected");
          isConnectingRef.current = false;
          ctx.setIsInitializingSandbox(false);
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
        },
      },
    );
  } catch (err) {
    if (!isCurrentStream()) {
      return;
    }
    if (err instanceof Error && err.name === "AbortError") {
      console.log("[SSE] Connection aborted");
      return;
    }
    console.error("[SSE] Connection error:", err);
    setConnectionStatus("disconnected");
    throw err;
  } finally {
    if (isCurrentStream()) {
      isConnectingRef.current = false;
    }
  }
}

/**
 * Smart reconnect with exponential backoff
 */
export async function reconnectSSE(
  ctx: SSEConnectionContext & {
    sessionIdRef: React.MutableRefObject<string | null>;
    currentRunIdRef: React.MutableRefObject<string | null>;
    isReconnectFromHistoryRef: React.MutableRefObject<boolean>;
  },
  dependencies: ReconnectDependencies = {},
): Promise<void> {
  const {
    sessionIdRef,
    currentRunIdRef,
    streamingMessageIdRef,
    abortControllerRef,
    isConnectingRef,
    reconnectTimeoutRef,
    retryCountRef,
    messagesRef,
    isReconnectFromHistoryRef,
    setConnectionStatus,
  } = ctx;
  const getStatus = dependencies.getStatus || sessionApi.getStatus;
  const connect = dependencies.connect || connectToSSE;

  const currentSessId = sessionIdRef.current;
  const currentRId = currentRunIdRef.current;
  const currentMsgId = streamingMessageIdRef.current;
  const reconnectStreamVersion = ctx.streamVersionRef.current;
  const isCurrentReconnect = () =>
    isCurrentSSETarget(
      ctx,
      currentSessId || "",
      currentRId || "",
      reconnectStreamVersion,
    );

  if (!currentSessId || !currentRId || !isCurrentReconnect()) {
    console.log("[SSE] No session/run ID, skipping reconnect");
    return;
  }

  clearReconnectTimeout(reconnectTimeoutRef);

  if (abortControllerRef.current) {
    abortControllerRef.current.abort();
    abortControllerRef.current = null;
  }

  isConnectingRef.current = false;

  try {
    const statusData = await getStatus(currentSessId, currentRId);
    if (!isCurrentReconnect()) {
      return;
    }
    const terminalStatus = terminalRunStatus(statusData.status);
    if (terminalStatus) {
      console.log("[SSE] Task already completed");
      ctx.onRunTerminal?.(
        currentRId,
        terminalStatus,
        currentMsgId || currentRId,
      );
      return;
    }
    if (!isActiveRunStatus(statusData.status)) {
      setConnectionStatus("disconnected");
      ctx.setIsInitializingSandbox(false);
      return;
    }
  } catch (err) {
    console.error("[SSE] Failed to check task status:", err);
    // Without a current authoritative run record, fail closed. A later user
    // reconnect can query again; this stale connection must not revive itself.
    setConnectionStatus("disconnected");
    ctx.setIsInitializingSandbox(false);
    return;
  }

  setConnectionStatus("reconnecting");

  const delay = getReconnectDelay(retryCountRef.current);
  retryCountRef.current += 1;
  console.log(
    `[SSE] Scheduling reconnect in ${delay}ms (retry ${retryCountRef.current})`,
  );

  reconnectTimeoutRef.current = setTimeout(async () => {
    if (!isCurrentReconnect()) {
      return;
    }
    if (currentMsgId) {
      const msgs = messagesRef.current;
      const lastMsg = msgs.find((m) => m.id === currentMsgId);
      if (lastMsg) {
        isReconnectFromHistoryRef.current = true;
        try {
          await connect(currentSessId, currentRId, currentMsgId, ctx);
        } catch {
          if (!isCurrentReconnect()) {
            return;
          }
          await reconnectSSE(ctx);
        }
      }
    }
  }, delay);
}

// Import Message type for messagesRef
import type { Message } from "../../types";
