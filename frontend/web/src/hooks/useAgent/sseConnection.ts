/**
 * SSE Connection utilities for useAgent hook
 * Handles SSE connection, reconnection, and stream management
 */

import { fetchEventSource } from "@microsoft/fetch-event-source";
import { sessionApi } from "../../services/api";
import {
  getValidAccessToken,
  refreshAccessToken,
} from "../../services/api/tokenManager";
import { getRefreshToken } from "../../services/api/token";
import {
  handlePublicRunStreamFrameV4,
  type EventHandlerContext,
} from "./eventHandlers";
import {
  comparePublicRunStreamCursors,
  type V4AdapterBinding,
  type V4SseFrame,
} from "../../components/chat/assistant-ui/publicEventAdapter";
import { clearAllLoadingStates } from "./messageParts";
import { collapsePublicExecutionSteps } from "./publicStreamPresentation";
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
export interface ReplayGapRecoveryOwner {
  sessionId: string;
  runId: string;
  streamVersion: number;
  promise: Promise<void>;
}

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
  replayGapRecoveryRef?: React.MutableRefObject<ReplayGapRecoveryOwner | null>;
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

export function isSSEHeartbeatComment(event: {
  event?: string;
  data?: string;
  id?: string;
}): boolean {
  return !event.event && !event.data && !event.id;
}

/** Injectable connection dependencies keep auth and startup races testable. */
export interface SSETokenDependencies {
  getValidAccessToken?: typeof getValidAccessToken;
  getRefreshToken?: typeof getRefreshToken;
  refreshAccessToken?: typeof refreshAccessToken;
  now?: () => number;
  setStartupTimeout?: typeof globalThis.setTimeout;
  clearStartupTimeout?: typeof globalThis.clearTimeout;
  replayGapDependencies?: ReconnectDependencies;
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

export const MAX_SSE_STARTUP_RETRIES = 3;
export const SSE_STARTUP_RETRY_BUDGET_MS = 10_000;
const SSE_STARTUP_RETRY_BASE_DELAY_MS = 250;
const SSE_RETRYABLE_STARTUP_CODES = new Set([
  "sse_stream_not_admitted",
  "sse_stream_not_confirmed",
]);

class RetryableSSEStartupError extends Error {
  constructor(readonly code: string) {
    super("sse_startup_not_ready");
    this.name = "RetryableSSEStartupError";
  }
}

class SSEStartupRetryExhaustedError extends Error {
  constructor() {
    super("sse_startup_retry_exhausted");
    this.name = "SSEStartupRetryExhaustedError";
  }
}

function retryableSSEStartupCode(response: Response): string | null {
  if (
    response.status !== 409 ||
    response.headers.get("X-SSE-Retryable") !== "true"
  ) {
    return null;
  }
  const code = response.headers.get("X-SSE-Error-Code") || "";
  return SSE_RETRYABLE_STARTUP_CODES.has(code) ? code : null;
}

function getSSEStartupRetryDelay(retryCount: number): number {
  return SSE_STARTUP_RETRY_BASE_DELAY_MS * 2 ** Math.max(0, retryCount - 1);
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

class SSEReplayGapError extends Error {
  constructor() {
    super("sse_replay_gap");
    this.name = "SSEReplayGapError";
  }
}

interface PendingV4TerminalHydration {
  semanticEventId: string;
  terminalEventId: string;
  promise: Promise<void>;
  resolve: () => void;
  duplicateTerminalCommits: Array<() => void>;
  pendingEndCommits: Array<() => void>;
}

function isSSEReplayGapError(error: unknown): error is SSEReplayGapError {
  return error instanceof SSEReplayGapError;
}

export const MAX_STATUS_QUERY_RETRIES = 2;
export const REPLAY_GAP_STATUS_POLL_DELAY_MS = 1_000;
/** Per-attempt ceiling for an authoritative run status read. */
export const AUTHORITATIVE_STATUS_ATTEMPT_TIMEOUT_MS = 8_000;
/** Maximum reconnects after continuous transport loss for one session/run. */
export const MAX_CONSECUTIVE_SSE_RECONNECTS = 3;
type ReconnectDependencies = {
  getStatus?: typeof sessionApi.getStatus;
  connect?: typeof connectToSSE;
  statusAttemptTimeoutMs?: number;
  reconnectDelay?: typeof getReconnectDelay;
  replayGapStatusPollDelayMs?: number;
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
  allowIdle = false,
  getStatus = sessionApi.getStatus,
  attemptTimeoutMs = AUTHORITATIVE_STATUS_ATTEMPT_TIMEOUT_MS,
}: {
  sessionId: string;
  runId: string;
  isCurrent: () => boolean;
  statusRetryCountRef: React.MutableRefObject<number>;
  allowIdle?: boolean;
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
      if (
        status &&
        ((allowIdle && status === "idle") ||
          isActiveRunStatus(status) ||
          terminalRunStatus(status))
      ) {
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

export async function recoverReplayGap(
  ctx: SSEConnectionContext,
  {
    sessionId,
    runId,
    messageId,
    streamVersion,
  }: {
    sessionId: string;
    runId: string;
    messageId: string;
    streamVersion: number;
  },
  dependencies: ReconnectDependencies = {},
): Promise<void> {
  const isCurrent = () =>
    isCurrentSSETarget(ctx, sessionId, runId, streamVersion);
  const existing = ctx.replayGapRecoveryRef?.current;
  if (
    existing &&
    existing.sessionId === sessionId &&
    existing.runId === runId &&
    existing.streamVersion === streamVersion
  ) {
    return existing.promise;
  }

  if (!isCurrent()) {
    return;
  }

  const acceptedStreamCursorRef = ctx.acceptedStreamCursorRef;
  const cursor = acceptedStreamCursorRef?.current;
  if (
    acceptedStreamCursorRef &&
    cursor?.sessionId === sessionId &&
    cursor.runId === runId
  ) {
    acceptedStreamCursorRef.current = {
      sessionId: null,
      runId: null,
      eventId: null,
      streamIncarnation: null,
    };
  }
  const acceptedRunEventSequenceRef = ctx.acceptedRunEventSequenceRef;
  const sequence = acceptedRunEventSequenceRef?.current;
  if (
    acceptedRunEventSequenceRef &&
    sequence?.sessionId === sessionId &&
    sequence.runId === runId
  ) {
    acceptedRunEventSequenceRef.current = {
      sessionId: null,
      runId: null,
      sequence: null,
    };
  }
  ctx.publicStreamPresentation?.flush({
    sessionId,
    runId,
    assistantMessageId: messageId,
    streamVersion,
  });
  ctx.publicStreamPresentation?.invalidate();
  ctx.setMessages((messages) =>
    messages.map((message) =>
      message.id === messageId ? { ...message, isStreaming: false } : message,
    ),
  );
  ctx.setConnectionStatus("recovering_gap");
  ctx.setIsInitializingSandbox(false);

  const owner: ReplayGapRecoveryOwner = {
    sessionId,
    runId,
    streamVersion,
    promise: Promise.resolve(),
  };
  const settleUnavailable = () => {
    if (!isCurrent()) {
      return;
    }
    if (ctx.onRunStatusUnavailable?.(runId, messageId)) {
      return;
    }
    ctx.setConnectionStatus("disconnected");
    ctx.setIsInitializingSandbox(false);
  };
  const delayMs = Math.max(
    0,
    dependencies.replayGapStatusPollDelayMs ?? REPLAY_GAP_STATUS_POLL_DELAY_MS,
  );
  const promise = (async () => {
    try {
      while (isCurrent()) {
        const statusResult = await queryAuthoritativeRunStatus({
          sessionId,
          runId,
          isCurrent,
          statusRetryCountRef: { current: 0 },
          getStatus: dependencies.getStatus,
          attemptTimeoutMs: dependencies.statusAttemptTimeoutMs,
        });
        if (statusResult.kind === "stale") {
          return;
        }
        if (statusResult.kind === "unavailable") {
          settleUnavailable();
          return;
        }
        const status = terminalRunStatus(statusResult.status);
        if (status) {
          if (!isCurrent()) {
            return;
          }
          if (ctx.hydrateTerminalRun) {
            await ctx.hydrateTerminalRun(sessionId, runId, status, messageId);
          } else {
            ctx.onRunTerminal?.(runId, status, messageId);
          }
          return;
        }
        await new Promise<void>((resolve) => setTimeout(resolve, delayMs));
        if (!isCurrent()) {
          return;
        }
      }
    } finally {
      if (ctx.replayGapRecoveryRef?.current === owner) {
        ctx.replayGapRecoveryRef.current = null;
      }
    }
  })();
  owner.promise = promise;
  if (ctx.replayGapRecoveryRef) {
    ctx.replayGapRecoveryRef.current = owner;
  }
  return promise;
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
  const now = tokenDependencies.now || Date.now;
  const setStartupTimeout =
    tokenDependencies.setStartupTimeout || globalThis.setTimeout;
  const clearStartupTimeout =
    tokenDependencies.clearStartupTimeout || globalThis.clearTimeout;

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
  ctx.publicStreamPresentation?.activate({
    sessionId: targetSessionId,
    runId: targetRunId,
    assistantMessageId: messageId,
    streamVersion,
  });
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
  const acceptedCursor = ctx.acceptedStreamCursorRef?.current;
  const acceptedCursorOwnsRun =
    acceptedCursor?.sessionId === targetSessionId &&
    acceptedCursor.runId === targetRunId;
  let acceptedStreamIncarnation = acceptedCursorOwnsRun
    ? (acceptedCursor.streamIncarnation ?? null)
    : null;
  if (acceptedCursorOwnsRun && acceptedCursor.eventId) {
    headers["Last-Event-ID"] = acceptedCursor.eventId;
  }

  console.log(
    `[SSE] Connecting: session=${targetSessionId}, run_id=${targetRunId}`,
  );

  let receivedTerminalEvent = false;
  let receivedNonTerminalApplicationError = false;
  let pendingTerminalHydration: PendingV4TerminalHydration | null = null;
  let startupRetryCount = 0;
  const startupRetryStartedAt = now();
  let startupDeadlineTimer: ReturnType<typeof globalThis.setTimeout> | null = null;
  const startupDeadline = new Promise<never>((_resolve, reject) => {
    startupDeadlineTimer = setStartupTimeout(() => {
      if (!isCurrentStream()) {
        return;
      }
      console.warn("[SSE] Startup retry exhausted", {
        code: "sse_startup_deadline_exceeded",
        attempts: startupRetryCount,
        elapsedMs: now() - startupRetryStartedAt,
        outcome: "exhausted",
      });
      streamAbortController.abort();
      reject(new SSEStartupRetryExhaustedError());
    }, SSE_STARTUP_RETRY_BUDGET_MS);
  });

  setConnectionStatus("connecting");

  const finalizeTerminalClose = () => {
    if (!isCurrentStream()) return;
    setConnectionStatus("disconnected");
    isConnectingRef.current = false;
    ctx.setIsInitializingSandbox(false);
    ctx.publicStreamPresentation?.flush({
      sessionId: targetSessionId,
      runId: targetRunId,
      assistantMessageId: messageId,
      streamVersion,
    });
    ctx.publicStreamPresentation?.invalidate();
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
  };

  try {
    await Promise.race([
      fetchStream(
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
          const startupCode = retryableSSEStartupCode(response);
          if (startupCode) {
            throw new RetryableSSEStartupError(startupCode);
          }
          if (!response.ok) {
            throw new Error(
              response.headers.get("X-SSE-Error-Code") ||
                `HTTP error! status: ${response.status}`,
            );
          }
          if (startupDeadlineTimer) {
            clearStartupTimeout(startupDeadlineTimer);
            startupDeadlineTimer = null;
          }
          console.log("[SSE] Connection established");
          setConnectionStatus("connected");
        },
        onmessage: (event) => {
          if (!isCurrentStream()) {
            return;
          }
          if (isSSEHeartbeatComment(event)) return;
          if (event.event === "ping") return;
          let parsed: unknown;
          try {
            parsed = JSON.parse(event.data);
          } catch {
            receivedNonTerminalApplicationError = true;
            throw new Error("sse_event_json_invalid");
          }
          const eventId = event.id;
          if (!eventId) {
            receivedNonTerminalApplicationError = true;
            throw new Error("sse_event_id_missing");
          }
          const candidateIncarnation =
            typeof parsed === "object" &&
            parsed !== null &&
            !Array.isArray(parsed) &&
            Number.isSafeInteger(
              (parsed as { stream_incarnation?: unknown }).stream_incarnation,
            )
              ? (parsed as { stream_incarnation: number }).stream_incarnation
              : null;
          if (candidateIncarnation === null || candidateIncarnation < 1) {
            receivedNonTerminalApplicationError = true;
            throw new Error("sse_event_contract_invalid");
          }
          acceptedStreamIncarnation ??= candidateIncarnation;
          const frame: V4SseFrame = {
            eventHeader: event.event || "",
            transportCursor: eventId,
            // streamVersion is the captured connection generation. It is kept
            // in the adapter frame only, never added to the wire envelope.
            generation: streamVersion,
            value: parsed,
          };
          const binding = {
            sessionId: targetSessionId,
            runId: targetRunId,
            streamVersion,
            streamIncarnation: acceptedStreamIncarnation,
            generation: streamVersion,
          };
          const adapterBinding: V4AdapterBinding = {
            runId: targetRunId,
            streamIncarnation: acceptedStreamIncarnation,
            generation: streamVersion,
          };
          const eventType =
            typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)
              ? (parsed as { event_type?: unknown }).event_type
              : null;
          const semanticEventId =
            typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)
              ? (parsed as { event_id?: unknown }).event_id
              : null;
          const payload =
            typeof parsed === "object" && parsed !== null && !Array.isArray(parsed) &&
            typeof (parsed as { payload?: unknown }).payload === "object" &&
            (parsed as { payload?: unknown }).payload !== null &&
            !Array.isArray((parsed as { payload?: unknown }).payload)
              ? ((parsed as { payload: Record<string, unknown> }).payload)
              : null;
          const terminalEventId =
            typeof payload?.terminal_event_id === "string"
              ? payload.terminal_event_id
              : null;
          const isRunTerminalEvent =
            eventType === "run.succeeded" ||
            eventType === "run.failed" ||
            eventType === "run.cancelled";
          const isStreamEndEvent = eventType === "stream.end";
          const provesRunProgress =
            typeof eventType === "string" &&
            !eventType.startsWith("stream.");
          const pendingBeforeFrame = pendingTerminalHydration;
          let createdPendingTerminal = false;
          if (
            isRunTerminalEvent &&
            typeof semanticEventId === "string" &&
            terminalEventId &&
            ctx.v4TerminalFenceRef &&
            !pendingTerminalHydration
          ) {
            let resolvePending!: () => void;
            const promise = new Promise<void>((resolve) => {
              resolvePending = resolve;
            });
            pendingTerminalHydration = {
              semanticEventId,
              terminalEventId,
              promise,
              resolve: resolvePending,
              duplicateTerminalCommits: [],
              pendingEndCommits: [],
            };
            createdPendingTerminal = true;
          }
          let transportCommitted = false;
          const commitTransportCursor = (semanticApplied: boolean) => {
            transportCommitted = true;
            if (isRunTerminalEvent || isStreamEndEvent) {
              receivedTerminalEvent = true;
            }
            if (!isCurrentStream()) return;
            const acceptedCursor = ctx.acceptedStreamCursorRef?.current;
            const ownsAcceptedCursor =
              acceptedCursor?.sessionId === targetSessionId &&
              acceptedCursor.runId === targetRunId;
            const cursorComparison =
              ownsAcceptedCursor && acceptedCursor.eventId
                ? comparePublicRunStreamCursors(eventId, acceptedCursor.eventId)
                : 1;
            if (
              ownsAcceptedCursor &&
              acceptedCursor.eventId &&
              (cursorComparison === null || cursorComparison <= 0)
            ) {
              if (semanticApplied && provesRunProgress) {
                retryCountRef.current = 0;
              }
              return;
            }
            if (ctx.acceptedStreamCursorRef) {
              ctx.acceptedStreamCursorRef.current = {
                sessionId: targetSessionId,
                runId: targetRunId,
                eventId,
                streamIncarnation: acceptedStreamIncarnation!,
              };
            }
            if (semanticApplied && provesRunProgress) {
              retryCountRef.current = 0;
            }
          };
          const commitAcceptedStreamEvent = (semanticApplied: boolean) => {
            commitTransportCursor(semanticApplied);
            if (!isRunTerminalEvent || !pendingTerminalHydration) return;
            const pending = pendingTerminalHydration;
            for (const commit of pending.duplicateTerminalCommits) commit();
            for (const commit of pending.pendingEndCommits) commit();
            if (ctx.v4TerminalFenceRef) ctx.v4TerminalFenceRef.current = null;
            ctx.v4TerminalEventIdsRef?.current.clear();
            pendingTerminalHydration = null;
            pending.resolve();
          };
          const accepted = handlePublicRunStreamFrameV4({
            frame,
            adapterBinding,
            messageId,
            ctx,
            binding,
            currentGeneration: streamVersion,
            onGap: () => {
              receivedNonTerminalApplicationError = true;
              throw new SSEReplayGapError();
            },
            onCommitted: commitAcceptedStreamEvent,
          });
          if (!accepted) {
            const matchesPendingTerminal = Boolean(
              pendingBeforeFrame &&
                terminalEventId === pendingBeforeFrame.terminalEventId &&
                semanticEventId === pendingBeforeFrame.semanticEventId,
            );
            if (isRunTerminalEvent && matchesPendingTerminal) {
              pendingBeforeFrame!.duplicateTerminalCommits.push(() =>
                commitTransportCursor(false),
              );
              return;
            }
            if (
              isStreamEndEvent &&
              pendingBeforeFrame &&
              terminalEventId === pendingBeforeFrame.terminalEventId
            ) {
              pendingBeforeFrame.pendingEndCommits.push(() =>
                commitTransportCursor(false),
              );
              return;
            }
            if (createdPendingTerminal) pendingTerminalHydration = null;
            if (transportCommitted) return;
            receivedNonTerminalApplicationError = true;
            throw new Error("sse_event_contract_invalid");
          }
        },
        onerror: (err) => {
          if (!isCurrentStream()) {
            return;
          }
          if (err instanceof RetryableSSEStartupError) {
            const elapsedMs = now() - startupRetryStartedAt;
            if (
              startupRetryCount >= MAX_SSE_STARTUP_RETRIES ||
              elapsedMs >= SSE_STARTUP_RETRY_BUDGET_MS
            ) {
              console.warn("[SSE] Startup retry exhausted", {
                code: err.code,
                attempts: startupRetryCount,
                elapsedMs,
                outcome: "exhausted",
              });
              throw new SSEStartupRetryExhaustedError();
            }
            startupRetryCount += 1;
            const delayMs = getSSEStartupRetryDelay(startupRetryCount);
            console.info("[SSE] Startup retry scheduled", {
              code: err.code,
              attempt: startupRetryCount,
              delayMs,
              elapsedMs,
              outcome: "scheduled",
            });
            setConnectionStatus("connecting");
            return delayMs;
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
            if (pendingTerminalHydration) {
              const pending = pendingTerminalHydration;
              void pending.promise.then(() => finalizeTerminalClose());
              return;
            }
            setConnectionStatus("reconnecting");
            throw new Error("SSE closed before terminal event");
          }
          finalizeTerminalClose();
        },
      },
    ),
      startupDeadline,
    ]);
  } catch (err) {
    if (isSSEReplayGapError(err)) {
      streamAbortController.abort();
      if (abortControllerRef.current === streamAbortController) {
        abortControllerRef.current = null;
      }
      isConnectingRef.current = false;
      await recoverReplayGap(
        ctx,
        {
          sessionId: targetSessionId,
          runId: targetRunId,
          messageId,
          streamVersion,
        },
        tokenDependencies.replayGapDependencies,
      );
      return;
    }
    if (isRefreshRetryRequested(err)) {
      // The signal is valid only for this exact captured controller and its
      // session/run/generation. A stale callback must not touch a replacement
      // stream's ref, connecting state, or status.
      if (!isCurrentStream()) {
        return;
      }
      ctx.publicStreamPresentation?.flush({
        sessionId: targetSessionId,
        runId: targetRunId,
        assistantMessageId: messageId,
        streamVersion,
      });
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
    if (startupDeadlineTimer) {
      clearStartupTimeout(startupDeadlineTimer);
    }
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

  if (currentMsgId) {
    ctx.publicStreamPresentation?.flush({
      sessionId: currentSessId,
      runId: currentRId,
      assistantMessageId: currentMsgId,
      streamVersion: reconnectStreamVersion,
    });
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
    convergeUnavailable();
    return;
  }

  const terminalStatus = terminalRunStatus(statusResult.status);
  if (terminalStatus) {
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
