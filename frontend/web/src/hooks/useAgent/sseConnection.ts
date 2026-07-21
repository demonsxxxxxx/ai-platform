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
import {
  isSequencedPublicChatEvent,
  type EventData,
  type EventType,
  type StreamEvent,
} from "./types";
import { handleStreamEvent, type EventHandlerContext } from "./eventHandlers";
import { clearAllLoadingStates } from "./messageParts";
import {
  authoritativeRunStatus,
  isActiveRunStatus,
  terminalRunStatus,
  terminalRunStatusFromEvent,
  type TerminalRunStatus,
} from "./runLifecycle";
import type { ChatRunStatusResponse } from "../../services/api/session";
import { formatSafeDiagnosticLog } from "../../utils/backendErrors";

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
  hydrateTerminalRun?: (
    sessionId: string,
    runId: string,
    status: TerminalRunStatus,
    messageId: string,
  ) => Promise<void>;
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

/**
 * A sanitized, explicit contract for authentication failures which cannot be
 * recovered by reconnecting this stream. Callers must converge locally rather
 * than treating these as ordinary transport interruptions.
 */
export const NON_RETRYABLE_SSE_AUTH_ERROR_CODE = "sse_authentication_failed";
export type NonRetryableSSEAuthenticationFailure =
  | "refresh_retry_exhausted"
  | "refresh_unavailable"
  | "refresh_failed";

/** Stable, sanitized authentication error surfaced to stream owners. */
export class NonRetryableSSEAuthenticationError extends Error {
  readonly code = NON_RETRYABLE_SSE_AUTH_ERROR_CODE;

  constructor(readonly failure: NonRetryableSSEAuthenticationFailure) {
    super(NON_RETRYABLE_SSE_AUTH_ERROR_CODE);
    this.name = "NonRetryableSSEAuthenticationError";
  }
}

/** Returns true only for the explicit non-retryable SSE auth contract. */
export function isNonRetryableSSEAuthenticationError(
  error: unknown,
): error is NonRetryableSSEAuthenticationError {
  if (!(error instanceof NonRetryableSSEAuthenticationError)) {
    return false;
  }
  return (
    error.code === NON_RETRYABLE_SSE_AUTH_ERROR_CODE &&
    (error.failure === "refresh_retry_exhausted" ||
      error.failure === "refresh_unavailable" ||
      error.failure === "refresh_failed")
  );
}

/**
 * Internal handoff only: the outer stream owner catches this after a current
 * token refresh succeeds, disposes its captured controller, and starts the
 * single refreshed attempt. It must never escape to hook consumers.
 */
class RefreshRetryRequested extends Error {
  constructor() {
    super("sse_refresh_retry_requested");
    this.name = "RefreshRetryRequested";
  }
}

function isRefreshRetryRequested(error: unknown): error is RefreshRetryRequested {
  return error instanceof RefreshRetryRequested;
}

export const MAX_STATUS_QUERY_RETRIES = 2;
/** Per-attempt ceiling for an authoritative run status read. */
export const AUTHORITATIVE_STATUS_ATTEMPT_TIMEOUT_MS = 8_000;
/** Maximum reconnects after continuous transport loss for one session/run. */
export const MAX_CONSECUTIVE_SSE_RECONNECTS = 3;
type ReconnectDependencies = {
  getStatus?: typeof sessionApi.getStatus;
  connect?: typeof connectToSSE;
  statusAttemptTimeoutMs?: number;
  reconnectDelay?: typeof getReconnectDelay;
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
  attemptTimeoutMs = AUTHORITATIVE_STATUS_ATTEMPT_TIMEOUT_MS,
}: {
  sessionId: string;
  runId: string;
  isCurrent: () => boolean;
  statusRetryCountRef: React.MutableRefObject<number>;
  getStatus?: typeof sessionApi.getStatus;
  attemptTimeoutMs?: number;
}): Promise<AuthoritativeStatusQueryResult> {
  while (isCurrent()) {
    const attemptAbortController = new AbortController();
    let attemptTimeout: ReturnType<typeof setTimeout> | null = null;
    try {
      const statusRequest = getStatus(sessionId, runId, {
        signal: attemptAbortController.signal,
      });
      const timeout = new Promise<never>((_resolve, reject) => {
        attemptTimeout = setTimeout(() => {
          attemptAbortController.abort();
          reject(new Error("authoritative_status_query_timed_out"));
        }, Math.max(1, attemptTimeoutMs));
      });
      // Promise.race installs rejection handlers on both inputs, so a request
      // implementation which ignores abort cannot later create an unhandled
      // rejection after this owner has converged.
      const data = await Promise.race([statusRequest, timeout]);
      if (!isCurrent()) {
        return { kind: "stale" };
      }
      const status = authoritativeRunStatus(data);
      if (status && (isActiveRunStatus(status) || terminalRunStatus(status))) {
        statusRetryCountRef.current = 0;
        return { kind: "resolved", data, status };
      }
      console.warn("[SSE] Authoritative run status is unknown");
    } catch (error) {
      if (!isCurrent()) {
        return { kind: "stale" };
      }
      console.error(
        formatSafeDiagnosticLog(
          "[SSE] Authoritative status check failed",
          error,
        ),
      );
    } finally {
      if (attemptTimeout !== null) {
        clearTimeout(attemptTimeout);
      }
      attemptAbortController.abort();
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
 * A persisted public event sequence is the only signal authoritative enough
 * to restore a reconnect budget. Synthetic and final snapshot frames may be
 * replayed without proving new backend progress.
 */
function isAcceptedRunProgress(
  eventType: string,
  data: Record<string, unknown>,
  terminal: boolean,
): boolean {
  return !terminal && isSequencedPublicChatEvent(eventType, data as EventData);
}

/** A transport heartbeat confirms liveness only; it cannot create chat text. */
function isRunHeartbeat(eventType: string, data: Record<string, unknown>): boolean {
  return (
    eventType === "heartbeat" &&
    typeof data.status === "string" &&
    data.status.trim().length > 0
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
              // The first attempt already granted the only refresh opportunity
              // for this stream. Do not turn a second 401 into a reconnect.
              throw new NonRetryableSSEAuthenticationError(
                "refresh_retry_exhausted",
              );
            }
            if (!getCurrentRefreshToken()) {
              throw new NonRetryableSSEAuthenticationError(
                "refresh_unavailable",
              );
            }
            try {
              await refreshCurrentAccessToken();
            } catch {
              throw new NonRetryableSSEAuthenticationError("refresh_failed");
            }
            // Refresh is asynchronous. A session switch, clear, unmount, or
            // replacement stream can happen while it is pending; an old
            // callback must not touch the replacement controller or state.
            if (!isCurrentStream()) {
              return;
            }
            // Do not abort or recurse here. fetch-event-source treats an
            // abort as a successful completion, which would detach a retry
            // launched inside this callback from the original owner promise.
            throw new RefreshRetryRequested();
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

          // The status-stream heartbeat is intentionally not an assistant
          // message event. It confirms liveness for this attached stream, but
          // cannot erase this generation's cumulative reconnect budget: a
          // heartbeat-then-close loop is not stable transport progress.
          if (isRunHeartbeat(event.event, parsedData)) {
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
          // A foreign, replayed, stale, or otherwise rejected terminal frame
          // must not suppress close reconciliation for the current run.
          if (terminalStatus && accepted) {
            receivedTerminalEvent = true;
          }
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
          console.error(
            formatSafeDiagnosticLog("[SSE] Connection failed", err),
          );
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
    if (isRefreshRetryRequested(err)) {
      // The signal is valid only for this exact captured controller and its
      // session/run/generation. A stale callback must not touch a replacement
      // stream's ref, connecting state, or status.
      if (!isCurrentStream()) {
        return;
      }
      // Release this owner's reference before aborting its controller so an
      // abort callback cannot observe itself as the current replacement.
      abortControllerRef.current = null;
      isConnectingRef.current = false;
      streamAbortController.abort();
      return await connectToSSE(
        targetSessionId,
        targetRunId,
        messageId,
        ctx,
        true,
        fetchStream,
        tokenDependencies,
      );
    }
    if (!isCurrentStream()) {
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
    console.error(formatSafeDiagnosticLog("[SSE] Connection failed", err));
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
    attemptTimeoutMs: dependencies.statusAttemptTimeoutMs,
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
    if (ctx.hydrateTerminalRun) {
      await ctx.hydrateTerminalRun(
        currentSessId,
        currentRId,
        terminalStatus,
        currentMsgId || currentRId,
      );
    } else {
      ctx.onRunTerminal?.(
        currentRId,
        terminalStatus,
        currentMsgId || currentRId,
      );
    }
    return;
  }

  if (retryCountRef.current >= MAX_CONSECUTIVE_SSE_RECONNECTS) {
    // The backend is still active, but this client has exhausted its bounded
    // transport recovery budget. Converge locally without inventing failure.
    convergeUnavailable();
    return;
  }

  setConnectionStatus("reconnecting");

  const delay = (dependencies.reconnectDelay || getReconnectDelay)(
    retryCountRef.current,
  );
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
        } catch (error) {
          if (!isCurrentReconnect()) {
            return;
          }
          if (isNonRetryableSSEAuthenticationError(error)) {
            // Authentication cannot be recovered by a status read or another
            // stream attempt. The lifecycle converger clears the generation's
            // active stream without fabricating a backend failed result.
            convergeUnavailable();
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
