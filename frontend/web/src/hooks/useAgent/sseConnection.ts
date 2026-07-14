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
  authoritativeRunStatus,
  isActiveRunStatus,
  terminalRunStatus,
  terminalRunStatusFromEvent,
} from "./runLifecycle";
import type { ChatRunStatusResponse } from "../../services/api/session";

/**
 * SSE Connection context
 */
export interface SSEConnectionContext extends EventHandlerContext {
  isMountedRef?: React.MutableRefObject<boolean>;
  abortControllerRef: React.MutableRefObject<AbortController | null>;
  isConnectingRef: React.MutableRefObject<boolean>;
  streamingMessageIdRef: React.MutableRefObject<string | null>;
  reconnectTimeoutRef: React.MutableRefObject<ReturnType<
    typeof setTimeout
  > | null>;
  retryCountRef: React.MutableRefObject<number>;
  statusRetryCountRef?: React.MutableRefObject<number>;
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
/** Injectable token operations keep the 401 handoff race testable. */
export interface SSETokenDependencies {
  getValidAccessToken?: typeof getValidAccessToken;
  getRefreshToken?: typeof getRefreshToken;
  refreshAccessToken?: typeof refreshAccessToken;
}
export const MAX_STATUS_QUERY_RETRIES = 2;
/** Maximum reconnects after continuous transport loss for one session/run. */
export const MAX_CONSECUTIVE_SSE_RECONNECTS = 3;
type ReconnectDependencies = {
  getStatus?: typeof sessionApi.getStatus;
  connect?: typeof connectToSSE;
};

export type AuthoritativeStatusQueryResult =
  | {
      kind: "resolved";
      data: ChatRunStatusResponse;
      status: string;
    }
  | { kind: "stale" }
  | { kind: "unavailable" };

/**
 * Read one run's authoritative state with the same bounded retry semantics
 * for initial history restoration and every SSE interruption.
 */
export async function queryAuthoritativeRunStatus({
  sessionId,
  runId,
  isCurrent,
  statusRetryCountRef,
  getStatus = sessionApi.getStatus,
}: {
  sessionId: string;
  runId: string;
  isCurrent: () => boolean;
  statusRetryCountRef: React.MutableRefObject<number>;
  getStatus?: typeof sessionApi.getStatus;
}): Promise<AuthoritativeStatusQueryResult> {
  while (isCurrent()) {
    try {
      const data = await getStatus(sessionId, runId);
      if (!isCurrent()) {
        return { kind: "stale" };
      }
      const status = authoritativeRunStatus(data);
      if (status && (isActiveRunStatus(status) || terminalRunStatus(status))) {
        statusRetryCountRef.current = 0;
        return { kind: "resolved", data, status };
      }
      console.warn("[SSE] Unknown authoritative run status:", status);
    } catch (error) {
      if (!isCurrent()) {
        return { kind: "stale" };
      }
      console.error("[SSE] Failed to check task status:", error);
    }

    if (statusRetryCountRef.current >= MAX_STATUS_QUERY_RETRIES) {
      return { kind: "unavailable" };
    }
    statusRetryCountRef.current += 1;
  }

  return { kind: "stale" };
}

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
    ctx.isMountedRef?.current !== false &&
    ctx.sessionIdRef.current === targetSessionId &&
    ctx.currentRunIdRef.current === targetRunId &&
    (streamVersion === undefined || ctx.streamVersionRef.current === streamVersion)
  );
}

export function isTerminalSSEEvent(eventType: string, data?: unknown): boolean {
  return Boolean(
    terminalRunStatusFromEvent(
      eventType,
      isRecord(data) ? data : {},
    ),
  );
}

function explicitRunId(data: Record<string, unknown>): string | null {
  return typeof data.run_id === "string" && data.run_id.trim()
    ? data.run_id
    : null;
}

/**
 * A persisted run_event sequence is the only progress signal authoritative
 * enough to restore a reconnect budget. Open, ping, synthetic frames, and
 * non-run events may be replayed without proving new backend progress.
 */
function isAcceptedRunProgress(
  eventType: string,
  data: Record<string, unknown>,
  terminal: boolean,
): boolean {
  return (
    !terminal &&
    eventType === "run_event" &&
    typeof data.sequence === "number" &&
    Number.isSafeInteger(data.sequence) &&
    data.sequence >= 0
  );
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
  tokenDependencies: SSETokenDependencies = {},
): Promise<void> {
  const {
    abortControllerRef,
    isConnectingRef,
    streamingMessageIdRef,
    setConnectionStatus,
    retryCountRef,
    streamVersionRef,
  } = ctx;
  const getCurrentAccessToken =
    tokenDependencies.getValidAccessToken || getValidAccessToken;
  const getCurrentRefreshToken = tokenDependencies.getRefreshToken || getRefreshToken;
  const refreshCurrentAccessToken =
    tokenDependencies.refreshAccessToken || refreshAccessToken;

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

  const token = await getCurrentAccessToken();
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
  let receivedNonTerminalApplicationError = false;
  // A retry connection replaces this stream's controller.  If that new,
  // current connection fails, preserve its error through this old stream's
  // guarded catch instead of incorrectly treating it as a stale no-op.
  let refreshRetryError: unknown = null;

  setConnectionStatus("connecting");

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
            if (!getCurrentRefreshToken()) {
              throw new Error("SSE unauthorized: no refresh token");
            }
            try {
              await refreshCurrentAccessToken();
            } catch {
              throw new Error("SSE unauthorized: token refresh failed");
            }
            // Refresh is asynchronous. A session switch, clear, unmount, or
            // replacement stream can happen while it is pending; an old
            // callback must not touch the replacement controller or state.
            if (!isCurrentStream()) {
              return;
            }
            // Abort only this callback's controller, never the shared ref:
            // it may already belong to a newer stream.
            streamAbortController.abort();
            if (
              abortControllerRef.current !== streamAbortController ||
              !isCurrentSSETarget(ctx, targetSessionId, targetRunId, streamVersion)
            ) {
              return;
            }
            abortControllerRef.current = null;
            isConnectingRef.current = false;
            try {
              await connectToSSE(
                targetSessionId,
                targetRunId,
                messageId,
                ctx,
                true,
                fetchStream,
                tokenDependencies,
              );
            } catch (error) {
              refreshRetryError = error;
              throw error;
            }
            return;
          }
          if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
          }
          console.log("[SSE] Connection established");
          setConnectionStatus("connected");
        },
        onmessage: (event) => {
          if (!isCurrentStream()) {
            return;
          }
          if (event.event === "ping") return;
          let parsedData: Record<string, unknown>;
          try {
            const parsed = JSON.parse(event.data);
            if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
              return;
            }
            parsedData = parsed as Record<string, unknown>;
          } catch {
            // Ignore parse errors
            return;
          }
          const eventId =
            event.id ||
            (typeof parsedData.event_id === "string" && parsedData.event_id.trim()
              ? parsedData.event_id
              : uuid());
          const sourceRunId = explicitRunId(parsedData);
          // An old explicit frame cannot end this connection or suppress its
          // reconnect. It is not safe to rebind an explicit foreign run.
          if (sourceRunId && sourceRunId !== targetRunId) {
            return;
          }

          const terminalStatus = terminalRunStatusFromEvent(
            event.event,
            parsedData,
          );
          // An SSE `error` frame can report a stream interruption while the
          // backend run is still active. Abort immediately and hand exactly
          // one generation-bound reconciliation to the caller; do not wait
          // for a server close or let fetch-event-source retry internally.
          if (event.event === "error" && !terminalStatus) {
            receivedNonTerminalApplicationError = true;
            setConnectionStatus("reconnecting");
            isConnectingRef.current = false;
            // Throw while this stream is still current. fetch-event-source
            // then runs its error path, disposes its internal request, and
            // rejects this connection rather than scheduling a retry. Calling
            // our signal's abort() first would resolve fetch-event-source and
            // silently skip the authoritative reconciliation below.
            throw new Error("SSE application interruption before terminal event");
          }
          // A `done` frame without an authoritative terminal status (for
          // example, `{ status: "timeout" }`) likewise cannot complete the
          // run locally.
          if (
            (event.event === "done" || event.event === "complete") &&
            !terminalStatus
          ) {
            return;
          }
          const normalizedData =
            terminalStatus && !sourceRunId
              ? { ...parsedData, run_id: targetRunId }
              : parsedData;
          if (terminalStatus) {
            receivedTerminalEvent = true;
          }
          const timestamp = normalizedData._timestamp as string | undefined;
          const streamEvent: StreamEvent = {
            event: event.event as EventType,
            data: JSON.stringify(normalizedData),
          };
          const accepted = handleStreamEvent(
            streamEvent,
            messageId,
            eventId,
            timestamp,
            ctx,
            {
              sessionId: targetSessionId,
              runId: targetRunId,
              streamVersion,
            },
          );
          if (
            accepted &&
            isAcceptedRunProgress(event.event, normalizedData, Boolean(terminalStatus))
          ) {
            retryCountRef.current = 0;
          }
        },
        onerror: (err) => {
          if (!isCurrentStream()) {
            return;
          }
          console.error("[SSE] Connection error:", err);
          setConnectionStatus("reconnecting");
          // fetch-event-source retries unless the handler throws. Let the
          // generation-aware caller reconcile authoritative status instead.
          throw err;
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
      if (refreshRetryError !== null) {
        throw refreshRetryError;
      }
      return;
    }
    if (
      err instanceof Error &&
      err.name === "AbortError" &&
      !receivedNonTerminalApplicationError
    ) {
      console.log("[SSE] Connection aborted");
      return;
    }
    console.error("[SSE] Connection error:", err);
    setConnectionStatus("disconnected");
    if (receivedNonTerminalApplicationError) {
      streamAbortController.abort();
      if (abortControllerRef.current === streamAbortController) {
        abortControllerRef.current = null;
      }
      isConnectingRef.current = false;
    }
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
    statusRetryCountRef: providedStatusRetryCountRef,
    messagesRef,
    isReconnectFromHistoryRef,
    setConnectionStatus,
  } = ctx;
  const statusRetryCountRef =
    providedStatusRetryCountRef || { current: MAX_STATUS_QUERY_RETRIES };
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
  const convergeUnavailable = () => {
    if (
      ctx.onRunStatusUnavailable?.(currentRId || "", currentMsgId || currentRId || "")
    ) {
      return;
    }
    setConnectionStatus("disconnected");
    ctx.setIsInitializingSandbox(false);
  };

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

  const statusResult = await queryAuthoritativeRunStatus({
    sessionId: currentSessId,
    runId: currentRId,
    isCurrent: isCurrentReconnect,
    statusRetryCountRef,
    getStatus: dependencies.getStatus,
  });
  if (statusResult.kind === "stale") {
    return;
  }
  if (statusResult.kind === "unavailable") {
    // The run's backend state remains unknown. Converge locally without
    // inventing a failed backend result; reloading the session is recovery.
    convergeUnavailable();
    return;
  }

  const terminalStatus = terminalRunStatus(statusResult.status);
  if (terminalStatus) {
    console.log("[SSE] Task already completed");
    ctx.onRunTerminal?.(
      currentRId,
      terminalStatus,
      currentMsgId || currentRId,
    );
    return;
  }

  if (retryCountRef.current >= MAX_CONSECUTIVE_SSE_RECONNECTS) {
    // The backend is still active, but this client has exhausted its bounded
    // transport recovery budget. Converge locally without inventing failure.
    convergeUnavailable();
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
          await reconnectSSE(ctx, dependencies);
        }
      }
    }
  }, delay);
}

// Import Message type for messagesRef
import type { Message } from "../../types";
