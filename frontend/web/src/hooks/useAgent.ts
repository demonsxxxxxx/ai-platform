/**
 * Main useAgent hook
 * Provides agent communication, message management, and SSE streaming
 */

import {
  useState,
  useCallback,
  useRef,
  useEffect,
  useLayoutEffect,
  useMemo,
} from "react";
import toast from "react-hot-toast";
import i18n from "../i18n";
import { uuid } from "../utils/uuid";
import type {
  Message,
  MessagePart,
  ConnectionStatus,
  MessageAttachment,
  SelectedSkillRequest,
} from "../types";
import {
  DEFAULT_CHAT_AGENT_ID,
  isChatStreamNeedsConfirmation,
  resolveChatSessionAgentId,
  sessionApi,
  type BackendSession,
  type CapabilitySuggestion,
  type ChatStreamResponse,
} from "../services/api";
import {
  CHAT_SUBMISSION_RESOLUTION_PROTOCOL_VERSION,
  type ChatSubmissionPreLedgerAbsenceResolution,
  type ChatSubmissionResolution,
} from "../services/api/session";
import { feedbackApi } from "../services/api/feedback";
import { getAccessToken } from "../services/api/token";
import { useAuth } from "../hooks/useAuth";
import {
  BROWSER_AUTH_INCARCINATION_EVENT,
  getBrowserAuthIncarnation,
} from "./browserAuthCoordinator";
import { Permission } from "../types/auth";
import {
  type UseAgentOptions,
  type SubagentStackItem,
  type HistoryEvent,
  type SubmissionOutcome,
  type UseAgentReturn,
} from "./useAgent/types";
import {
  ensureTerminalAssistantSegment,
  reconstructMessagesFromEvents,
  getLastEventTimestamp,
  mergeHydratedRunSegment,
  prepareMessagesForRunningRun,
} from "./useAgent/historyLoader";
import {
  beginHistoryLoad,
  isCurrentHistoryLoad,
  resolveHistoryCurrentRunId,
} from "./useAgent/historyRunState";
import {
  isActiveRunStatus,
  terminalRunStatus,
  type TerminalRunStatus,
} from "./useAgent/runLifecycle";
import { clearAllLoadingStates } from "./useAgent/messageParts";
import {
  type AcceptedRunEventSequence,
  type EventHandlerContext,
} from "./useAgent/eventHandlers";
import {
  connectToSSE,
  reconnectSSE,
  clearReconnectTimeout,
  isNonRetryableSSEAuthenticationError,
  queryAuthoritativeRunStatus,
  type SSEConnectionContext,
} from "./useAgent/sseConnection";
import { createOptimisticMessagesForSend } from "./useAgent/optimisticMessages";
import {
  translateBackendError,
  translateChatAdmissionError,
} from "../utils/backendErrors";
import { dispatchSessionTitleUpdated } from "../utils/sessionTitleEvents";
import { ApiRequestError } from "../services/api/fetch";
import {
  SELECTED_SKILL_RECOVERABLE_CODES,
  type SelectedSkillRecoverableCode,
} from "./useSelectedSkillTask";
import {
  RunControlLifecycle,
  type RunControlAuthIdentity,
  type RunControlChild,
  type RunControlOwner,
  type RunControlParentIdentity,
} from "./useAgent/runControlLifecycle";

function getSelectedSkillRecoverableCode(
  error: unknown,
): SelectedSkillRecoverableCode | null {
  if (!(error instanceof ApiRequestError)) return null;
  return (
    SELECTED_SKILL_RECOVERABLE_CODES.find(
      (code) => error.code === code,
    ) ?? null
  );
}

function isProvenPrePersistenceChatRejection(error: unknown): boolean {
  return (
    error instanceof ApiRequestError &&
    error.status >= 400 &&
    error.status < 500 &&
    error.submissionDisposition === "rejected_before_persist"
  );
}

function formatChatSubmissionError(error: unknown): string {
  if (error instanceof ApiRequestError) {
    return translateChatAdmissionError(
      {
        status: error.status,
        code: error.code,
        message: error.message,
      },
      i18n.t.bind(i18n),
    );
  }
  return error instanceof Error
    ? translateBackendError(error.message, i18n.t.bind(i18n))
    : i18n.t("chat.unknownError");
}

function parseChatSubmissionResolution(
  value: unknown,
  submissionId: string,
): ChatSubmissionResolution | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const candidate = value as Record<string, unknown>;
  const protocolVersion = candidate.protocol_version;
  if (
    protocolVersion !== undefined &&
    protocolVersion !== CHAT_SUBMISSION_RESOLUTION_PROTOCOL_VERSION
  ) {
    return null;
  }
  if (candidate.submission_id !== submissionId) {
    return null;
  }
  if (candidate.state === "absent_before_ledger") {
    return protocolVersion === CHAT_SUBMISSION_RESOLUTION_PROTOCOL_VERSION
      ? (candidate as unknown as ChatSubmissionPreLedgerAbsenceResolution)
      : null;
  }

  if (candidate.state === "enqueue_failed") {
    return (
      (candidate.submission_disposition === undefined ||
        candidate.submission_disposition === null) &&
      candidate.rejection_code === "queue_enqueue_failed" &&
      (candidate.outcome === undefined || candidate.outcome === null)
    )
      ? (candidate as unknown as ChatSubmissionResolution)
      : null;
  }

  if (candidate.state === "rejected_before_persist") {
    return candidate.submission_disposition === "rejected_before_persist" &&
      typeof candidate.rejection_code === "string" &&
      candidate.rejection_code.trim().length > 0 &&
      (candidate.outcome === undefined || candidate.outcome === null)
      ? (candidate as unknown as ChatSubmissionResolution)
      : null;
  }

  if (
    candidate.state !== "queued" &&
    candidate.state !== "accepted_pending_enqueue" &&
    candidate.state !== "needs_confirmation"
  ) {
    return null;
  }

  const outcome = candidate.outcome;
  if (outcome === null || typeof outcome !== "object" || Array.isArray(outcome)) {
    return null;
  }
  const outcomeRecord = outcome as Record<string, unknown>;
  if (outcomeRecord.submission_id !== submissionId) return null;

  if (
    candidate.state === "queued" ||
    candidate.state === "accepted_pending_enqueue"
  ) {
    return outcomeRecord.status === candidate.state &&
      typeof outcomeRecord.session_id === "string" &&
      outcomeRecord.session_id.length > 0 &&
      typeof outcomeRecord.run_id === "string" &&
      outcomeRecord.run_id.length > 0
      ? (candidate as unknown as ChatSubmissionResolution)
      : null;
  }

  if (
    outcomeRecord.status !== "needs_confirmation" ||
    !Array.isArray(outcomeRecord.suggestions)
  ) {
    return null;
  }
  const suggestionsAreValid = outcomeRecord.suggestions.every((suggestion) => {
    if (suggestion === null || typeof suggestion !== "object" || Array.isArray(suggestion)) {
      return false;
    }
    const suggestionRecord = suggestion as Record<string, unknown>;
    return (
      typeof suggestionRecord.capability_id === "string" &&
      suggestionRecord.capability_id.length > 0 &&
      typeof suggestionRecord.label === "string" &&
      suggestionRecord.label.length > 0 &&
      typeof suggestionRecord.reason === "string"
    );
  });
  if (!suggestionsAreValid) return null;
  return candidate as unknown as ChatSubmissionResolution;
}

function isAuthoritativePreLedgerAbsence(
  resolution: ChatSubmissionResolution,
): resolution is ChatSubmissionPreLedgerAbsenceResolution {
  return resolution.state === "absent_before_ledger";
}

function formatConfirmationMessage(suggestions: CapabilitySuggestion[]): string {
  if (suggestions.length === 0) {
    return "需要确认处理方式后再继续。";
  }

  const items = suggestions.map((item, index) => {
    const reason = item.reason ? `：${item.reason}` : "";
    return `${index + 1}. ${item.label}${reason}`;
  });
  return ["需要确认处理方式后再继续。", "", ...items].join("\n");
}

function projectOwnedConfirmationMessages(
  messages: Message[],
  suggestions: CapabilitySuggestion[],
  assistantMessageId: string,
): Message[] | null {
  const confirmationMessage = formatConfirmationMessage(suggestions);
  const project = (message: Message): Message => ({
    ...message,
    content: confirmationMessage,
    isStreaming: false,
    parts: [{ type: "text", content: confirmationMessage }],
  });
  if (!messages.some((message) => message.id === assistantMessageId)) {
    return null;
  }
  return messages.map((message) =>
    message.id === assistantMessageId ? project(message) : message,
  );
}

function maxAcceptedRunEventSequence(
  events: HistoryEvent[] | undefined,
  runId: string | null,
): number | null {
  if (!runId || !events?.length) {
    return null;
  }

  let maximum: number | null = null;
  for (const event of events) {
    const data =
      typeof event.data === "object" && event.data !== null && !Array.isArray(event.data)
        ? (event.data as Record<string, unknown>)
        : null;
    const eventRunId =
      typeof event.run_id === "string" && event.run_id.trim()
        ? event.run_id
        : typeof data?.run_id === "string" && data.run_id.trim()
          ? data.run_id
          : null;
    // The production history wire contract keeps the persisted cursor at the
    // top level.  Retain the nested form only as a strict compatibility
    // fallback for older saved projections; synthetic answer entries have
    // neither form and therefore cannot restore a reconnect budget.
    const topLevelSequence = event.sequence;
    const fallbackSequence = data?.sequence;
    const sequence =
      typeof topLevelSequence === "number" &&
      Number.isSafeInteger(topLevelSequence) &&
      topLevelSequence >= 0
        ? topLevelSequence
        : typeof fallbackSequence === "number" &&
            Number.isSafeInteger(fallbackSequence) &&
            fallbackSequence >= 0
          ? fallbackSequence
          : null;
    if (
      eventRunId === runId &&
      sequence !== null &&
      (maximum === null || sequence > maximum)
    ) {
      maximum = sequence;
    }
  }
  return maximum;
}

interface ReconcileOwner {
  sessionId: string;
  runId: string;
  streamVersion: number;
  promise: Promise<void>;
}

type TerminalHydrationOwner = ReconcileOwner;

type AuthScope = readonly [tenantId: string, userId: string];

interface SubmissionUncertainty {
  sessionId: string | null;
  submissionId: string;
  owner: AuthScope;
  previousMessages?: Message[];
  suppressMessageProjection?: boolean;
}

interface ActivePreAdmissionSubmission {
  owner: AuthScope;
  submissionId: string;
  sessionId: string | null;
  previousMessages: Message[];
  token: number;
}

interface PersistedSubmissionReference {
  version: 1;
  owner: AuthScope;
  submissionId: string;
}

interface ConfirmationRecovery {
  owner: AuthScope;
  submissionId: string;
  suggestions: CapabilitySuggestion[];
}

const LEGACY_CHAT_SUBMISSION_STORAGE_KEY =
  "ai_platform_chat_submission_references_v1";
const CHAT_SUBMISSION_STORAGE_PREFIX = "ai_platform_chat_submission_reference_v1:";
const MAX_PERSISTED_OWNER_ID_LENGTH = 128;
const CANONICAL_SUBMISSION_ID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

function isPersistedOwnerId(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value.length <= MAX_PERSISTED_OWNER_ID_LENGTH &&
    value.trim().length === value.length
  );
}

function parsePersistedSubmissionReference(
  value: unknown,
): PersistedSubmissionReference | null {
  if (
    value === null ||
    typeof value !== "object" ||
    (value as { version?: unknown }).version !== 1 ||
    !Array.isArray((value as { owner?: unknown }).owner) ||
    (value as { owner: unknown[] }).owner.length !== 2 ||
    !isPersistedOwnerId((value as { owner: unknown[] }).owner[0]) ||
    !isPersistedOwnerId((value as { owner: unknown[] }).owner[1]) ||
    typeof (value as { submissionId?: unknown }).submissionId !== "string" ||
    !CANONICAL_SUBMISSION_ID_PATTERN.test(
      (value as { submissionId: string }).submissionId,
    )
  ) {
    return null;
  }
  return {
    version: 1,
    owner: [
      (value as { owner: string[] }).owner[0],
      (value as { owner: string[] }).owner[1],
    ],
    submissionId: (value as { submissionId: string }).submissionId,
  };
}

function submissionReferenceStorageKey(reference: PersistedSubmissionReference): string {
  return `${CHAT_SUBMISSION_STORAGE_PREFIX}${encodeURIComponent(reference.owner[0])}:${encodeURIComponent(reference.owner[1])}:${encodeURIComponent(reference.submissionId)}`;
}

function submissionReferenceIdentity(reference: PersistedSubmissionReference): string {
  return `${reference.owner[0]}\u0000${reference.owner[1]}\u0000${reference.submissionId}`;
}

function parseSubmissionReferenceJson(raw: string | null): PersistedSubmissionReference | null {
  if (!raw) return null;
  try {
    return parsePersistedSubmissionReference(JSON.parse(raw));
  } catch {
    return null;
  }
}

function quarantinePersistedSubmissionReference(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    // A storage failure still leaves sends fail-closed at their verified write.
  }
}

function readPersistedSubmissionReferences(): PersistedSubmissionReference[] {
  try {
    const references = new Map<string, PersistedSubmissionReference>();
    const legacyRaw = localStorage.getItem(LEGACY_CHAT_SUBMISSION_STORAGE_KEY);
    let legacy: unknown = null;
    if (legacyRaw !== null) {
      try {
        legacy = JSON.parse(legacyRaw);
      } catch {
        quarantinePersistedSubmissionReference(LEGACY_CHAT_SUBMISSION_STORAGE_KEY);
      }
    }
    if (Array.isArray(legacy)) {
      const retained: PersistedSubmissionReference[] = [];
      for (const item of legacy) {
        const parsed = parsePersistedSubmissionReference(item);
        if (!parsed) continue;
        retained.push(parsed);
        references.set(submissionReferenceIdentity(parsed), parsed);
      }
      if (retained.length !== legacy.length) {
        try {
          if (retained.length === 0) {
            localStorage.removeItem(LEGACY_CHAT_SUBMISSION_STORAGE_KEY);
          } else {
            localStorage.setItem(
              LEGACY_CHAT_SUBMISSION_STORAGE_KEY,
              JSON.stringify(retained),
            );
          }
        } catch {
          // A failed quarantine cannot authorize a later chat POST.
        }
      }
    } else if (legacyRaw !== null) {
      quarantinePersistedSubmissionReference(LEGACY_CHAT_SUBMISSION_STORAGE_KEY);
    }
    const independentKeys: string[] = [];
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index);
      if (key?.startsWith(CHAT_SUBMISSION_STORAGE_PREFIX)) {
        independentKeys.push(key);
      }
    }
    for (const key of independentKeys) {
      const raw = localStorage.getItem(key);
      const parsed = parseSubmissionReferenceJson(raw);
      if (
        !parsed ||
        submissionReferenceStorageKey(parsed) !== key
      ) {
        quarantinePersistedSubmissionReference(key);
        continue;
      }
      references.set(submissionReferenceIdentity(parsed), parsed);
    }
    return [...references.values()].sort((left, right) =>
      submissionReferenceIdentity(left).localeCompare(submissionReferenceIdentity(right)),
    );
  } catch {
    return [];
  }
}

function persistSubmissionReference(reference: PersistedSubmissionReference): boolean {
  try {
    const key = submissionReferenceStorageKey(reference);
    const encoded = JSON.stringify(reference);
    localStorage.setItem(key, encoded);
    const confirmed = localStorage.getItem(key);
    const parsed = confirmed ? parsePersistedSubmissionReference(JSON.parse(confirmed)) : null;
    return (
      parsed !== null &&
      authScopesEqual(parsed.owner, reference.owner) &&
      parsed.submissionId === reference.submissionId
    );
  } catch {
    // A private-mode quota failure cannot make an unknown mutation safe to retry.
    return false;
  }
}

function removePersistedSubmissionReference(owner: AuthScope, submissionId: string): void {
  try {
    const reference: PersistedSubmissionReference = { version: 1, owner, submissionId };
    localStorage.removeItem(submissionReferenceStorageKey(reference));
    const legacyRaw = localStorage.getItem(LEGACY_CHAT_SUBMISSION_STORAGE_KEY);
    const legacy = legacyRaw ? JSON.parse(legacyRaw) : [];
    if (!Array.isArray(legacy)) return;
    const retained = legacy.filter((item) => {
      const parsed = parsePersistedSubmissionReference(item);
      return !(
        parsed !== null &&
        authScopesEqual(parsed.owner, owner) &&
        parsed.submissionId === submissionId
      );
    });
    if (retained.length === 0) {
      localStorage.removeItem(LEGACY_CHAT_SUBMISSION_STORAGE_KEY);
    } else {
      localStorage.setItem(LEGACY_CHAT_SUBMISSION_STORAGE_KEY, JSON.stringify(retained));
    }
  } catch {
    // A failed cleanup keeps the reference fenced; it must not authorize replay.
  }
}

function authScopesEqual(left: AuthScope | null, right: AuthScope | null): boolean {
  return (
    left === right ||
    (left !== null &&
      right !== null &&
      left[0] === right[0] &&
      left[1] === right[1])
  );
}

function normalizeRunControlClaims(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.filter((item): item is string => typeof item === "string")
    .map((item) => item.trim())
    .filter(Boolean))].sort();
}

function runControlAuthKey(identity: RunControlAuthIdentity): string {
  return JSON.stringify([
    identity.incarnation,
    identity.sessionMarker,
    identity.tenantId,
    identity.userId,
    identity.roles,
    identity.permissions,
    identity.isAdmin,
    identity.isActive,
  ]);
}

/** A terminal result must not leave the composer blocked on a stalled history read. */
const TERMINAL_HISTORY_HYDRATION_TIMEOUT_MS = 10_000;

export function useAgent(options?: UseAgentOptions): UseAgentReturn {
  const {
    hasAnyPermission,
    isAuthenticated,
    isLoading: isAuthLoading,
    user,
  } = useAuth();
  const [browserAuthIncarnation, setBrowserAuthIncarnation] = useState(
    getBrowserAuthIncarnation,
  );
  const canReadFeedback = hasAnyPermission([
    Permission.FEEDBACK_READ,
    Permission.FEEDBACK_WRITE,
  ]);
  const runControlAuth = useMemo<RunControlAuthIdentity>(
    () => ({
      incarnation: browserAuthIncarnation,
      sessionMarker: getAccessToken(),
      tenantId: user?.tenant_id ?? "",
      userId: user?.id ?? "",
      roles: normalizeRunControlClaims(user?.roles),
      permissions: normalizeRunControlClaims(user?.permissions),
      isAdmin: user?.is_admin === true,
      isActive: user?.is_active === true,
    }),
    [browserAuthIncarnation, user],
  );
  const runControlAuthIdentity = useMemo(
    () => runControlAuthKey(runControlAuth),
    [runControlAuth],
  );

  // State
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessionAgentId, setSessionAgentId] = useState(DEFAULT_CHAT_AGENT_ID);
  const [error, setError] = useState<string | null>(null);
  const [connectionStatus, setConnectionStatus] =
    useState<ConnectionStatus>("disconnected");
  const [currentRunId, setCurrentRunId] = useState<string | null>(null);
  const [newlyCreatedSession, setNewlyCreatedSession] =
    useState<BackendSession | null>(null);
  const [isInitializingSandbox, setIsInitializingSandbox] = useState(false);
  const [sandboxError, setSandboxError] = useState<string | null>(null);

  // Refs for connection management
  const abortControllerRef = useRef<AbortController | null>(null);
  const isConnectingRef = useRef(false);
  const isLoadingHistoryRef = useRef(false);
  const isSendingRef = useRef(false);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(
    null,
  );
  const retryCountRef = useRef(0);
  const statusRetryCountRef = useRef(0);
  const isMountedRef = useRef(false);
  const mountedGenerationRef = useRef(0);
  const chatHistoryGenerationRef = useRef(0);
  const runControlAuthRevisionRef = useRef(0);
  const runControlAuthIdentityRef = useRef<string | null>(null);
  const runControlAuthEventRevisionRef = useRef(0);
  const appliedRunControlAuthEventRevisionRef = useRef(0);
  const runControlParentRef = useRef<{
    sessionId: string;
    runId: string;
  } | null>(null);
  const runControlLifecycleRef = useRef<RunControlLifecycle | null>(null);
  if (runControlLifecycleRef.current === null) {
    runControlLifecycleRef.current = new RunControlLifecycle();
  }
  const runControlLifecycle = runControlLifecycleRef.current;

  // Track processed event IDs to prevent duplicates
  const processedEventIdsRef = useRef<Set<string>>(new Set());

  // A persistent, per-session/run sequence cursor keeps reconnect recovery
  // replay-safe even after the bounded event-id set reaches its memory cap.
  const acceptedRunEventSequenceRef = useRef<AcceptedRunEventSequence>({
    sessionId: null,
    runId: null,
    sequence: null,
  });

  // Track last event timestamp from history
  const lastHistoryTimestampRef = useRef<Date | null>(null);

  // Subagent tracking stack
  const activeSubagentStackRef = useRef<SubagentStackItem[]>([]);

  // Current streaming message ID
  const streamingMessageIdRef = useRef<string | null>(null);

  // Flag for reconnect from history
  const isReconnectFromHistoryRef = useRef<boolean>(false);

  // Monotonic token used to stop stale overlapping loadHistory calls.
  const historyLoadTokenRef = useRef(0);

  // Session changes invalidate both pending submit responses and their SSE stream.
  const sessionGenerationRef = useRef(0);
  const submissionTokenRef = useRef(0);
  const authScopeRef = useRef<AuthScope | null>(null);
  const authScopeGenerationRef = useRef(0);
  const submissionUncertaintyRef = useRef<SubmissionUncertainty | null>(null);
  const submissionResolverOwnerRef = useRef<string | null>(null);
  // An incarnation event proves that the browser credential context changed,
  // but the authoritative principal can still be awaiting hydration.
  const submissionAuthIncarnationFenceRef = useRef<string | null>(null);
  const activePreAdmissionSubmissionRef =
    useRef<ActivePreAdmissionSubmission | null>(null);
  const [pendingSubmissionId, setPendingSubmissionId] = useState<string | null>(null);
  // Resolver-only confirmation has no transcript owner. Keep it separate
  // until a later user action rather than inventing an assistant turn.
  const [confirmationRecovery, setConfirmationRecovery] =
    useState<ConfirmationRecovery | null>(null);

  // Stream version to invalidate stale SSE events after clearMessages
  const streamVersionRef = useRef(0);
  // One owner covers concurrent online/visibility/history/transport recovery
  // for the same session/run/generation.
  const reconcileOwnerRef = useRef<ReconcileOwner | null>(null);
  const terminalHydrationOwnerRef = useRef<TerminalHydrationOwner | null>(null);

  // Keep sessionId/runId in ref for closure access
  const sessionIdRef = useRef<string | null>(null);
  const sessionAgentIdRef = useRef(DEFAULT_CHAT_AGENT_ID);
  const currentRunIdRef = useRef<string | null>(null);
  const messagesRef = useRef<Message[]>([]);

  useEffect(() => {
    sessionIdRef.current = sessionId;
  }, [sessionId]);

  useEffect(() => {
    sessionAgentIdRef.current = sessionAgentId;
  }, [sessionAgentId]);

  useEffect(() => {
    currentRunIdRef.current = currentRunId;
  }, [currentRunId]);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  const clearActivePreAdmissionSubmission = useCallback(
    (expectedToken?: number) => {
      const active = activePreAdmissionSubmissionRef.current;
      if (!active || (expectedToken !== undefined && active.token !== expectedToken)) {
        return false;
      }
      activePreAdmissionSubmissionRef.current = null;
      return true;
    },
    [],
  );

  const handoffActivePreAdmissionSubmission = useCallback(
    ({
      expectedToken,
      messages,
      projectMessages = true,
      requireCurrentToken = false,
    }: {
      expectedToken?: number;
      messages?: Message[];
      projectMessages?: boolean;
      requireCurrentToken?: boolean;
    } = {}) => {
      const active = activePreAdmissionSubmissionRef.current;
      if (!active || (expectedToken !== undefined && active.token !== expectedToken)) {
        return false;
      }
      activePreAdmissionSubmissionRef.current = null;
      if (
        (requireCurrentToken && submissionTokenRef.current !== active.token) ||
        authScopeRef.current !== active.owner
      ) {
        return false;
      }
      const recoveryMessages = messages ?? active.previousMessages;
      if (projectMessages) {
        messagesRef.current = recoveryMessages;
        setMessages(recoveryMessages);
      }
      submissionUncertaintyRef.current = {
        sessionId: active.sessionId,
        submissionId: active.submissionId,
        owner: active.owner,
        previousMessages: active.previousMessages,
        suppressMessageProjection: !projectMessages,
      };
      setPendingSubmissionId(active.submissionId);
      setError(
        i18n.t("chat.runTerminal.statusUnavailable", {
          defaultValue: i18n.t("chat.requestFailed"),
        }),
      );
      return true;
    },
    [],
  );

  const currentRunControlParent = useCallback(
    (target = runControlParentRef.current): RunControlParentIdentity | null => {
      if (!target || !isMountedRef.current) return null;
      return {
        chatHistoryGeneration: chatHistoryGenerationRef.current,
        authRevision: runControlAuthRevisionRef.current,
        auth: runControlAuth,
        sessionId: target.sessionId,
        runId: target.runId,
      };
    },
    [runControlAuth],
  );

  const bindRunControlParent = useCallback(
    (nextSessionId: string, nextRunId: string) => {
      runControlParentRef.current = {
        sessionId: nextSessionId,
        runId: nextRunId,
      };
      const parent = currentRunControlParent(runControlParentRef.current);
      if (parent) {
        runControlLifecycle.bindParent(parent);
      }
    },
    [currentRunControlParent, runControlLifecycle],
  );

  const invalidateRunControl = useCallback(
    ({ preserveParent = false }: { preserveParent?: boolean } = {}) => {
      chatHistoryGenerationRef.current += 1;
      runControlLifecycle.invalidate();
      if (!preserveParent) {
        runControlParentRef.current = null;
      }
    },
    [runControlLifecycle],
  );

  useLayoutEffect(() => {
    const handleAuthIncarnationChange = (event: Event) => {
      const detail = (event as CustomEvent<{ incarnation?: unknown }>).detail;
      const incarnation =
        typeof detail?.incarnation === "string"
          ? detail.incarnation
          : getBrowserAuthIncarnation();
      // This handler is the same-tab ownership seam. Abort/invalidate before
      // scheduling React work so a role refresh, login or logout cannot leave
      // an old parent alive long enough to begin a mutation.
      const hadActivePreAdmission =
        activePreAdmissionSubmissionRef.current !== null;
      handoffActivePreAdmissionSubmission();
      submissionAuthIncarnationFenceRef.current = incarnation;
      authScopeGenerationRef.current += 1;
      submissionResolverOwnerRef.current = null;
      submissionTokenRef.current += 1;
      isSendingRef.current = false;
      if (hadActivePreAdmission) {
        setIsLoading(false);
      }
      runControlAuthEventRevisionRef.current += 1;
      runControlAuthRevisionRef.current += 1;
      invalidateRunControl();
      setBrowserAuthIncarnation(incarnation);
    };
    window.addEventListener(
      BROWSER_AUTH_INCARCINATION_EVENT,
      handleAuthIncarnationChange,
    );
    return () =>
      window.removeEventListener(
        BROWSER_AUTH_INCARCINATION_EVENT,
        handleAuthIncarnationChange,
      );
  }, [handoffActivePreAdmissionSubmission, invalidateRunControl]);

  const clearReconcileOwners = useCallback(() => {
    reconcileOwnerRef.current = null;
    terminalHydrationOwnerRef.current = null;
  }, []);

  const convergeRunLifecycle = useCallback(
    (
      runId: string,
      outcome:
        | TerminalRunStatus
        | "status_unavailable"
        | "terminal_result_unavailable",
      messageId: string,
    ): boolean => {
      if (!isMountedRef.current || currentRunIdRef.current !== runId) {
        return false;
      }

      currentRunIdRef.current = null;
      clearReconcileOwners();
      streamVersionRef.current += 1;
      clearReconnectTimeout(reconnectTimeoutRef);
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
        abortControllerRef.current = null;
      }
      isConnectingRef.current = false;
      streamingMessageIdRef.current = null;
      isSendingRef.current = false;
      retryCountRef.current = 0;
      statusRetryCountRef.current = 0;
      acceptedRunEventSequenceRef.current = {
        sessionId: null,
        runId: null,
        sequence: null,
      };

      toast.dismiss("chat-queue");
      setCurrentRunId(null);
      setIsLoading(false);
      setConnectionStatus("disconnected");
      setIsInitializingSandbox(false);
      setSandboxError(null);
      options?.onClearApprovals?.();
      const productCard = (): MessagePart | null => {
        if (outcome === "failed") {
          return {
            type: "run_status",
            event_id: `terminal-failure:${runId}`,
            event_type: "run_failed",
            stage: "agent",
            message: i18n.t("chat.runTerminal.failed"),
            severity: "error",
          };
        }
        if (outcome === "status_unavailable") {
          return {
            type: "run_status",
            event_id: `terminal-status-unavailable:${runId}`,
            event_type: "status_unavailable",
            stage: "agent",
            message: i18n.t("chat.runTerminal.statusUnavailable", {
              defaultValue: i18n.t("chat.requestFailed"),
            }),
            severity: "warning",
          };
        }
        if (outcome === "terminal_result_unavailable") {
          return {
            type: "run_status",
            event_id: `terminal-result-unavailable:${runId}`,
            event_type: "terminal_result_unavailable",
            stage: "agent",
            message: i18n.t("chat.runTerminal.resultUnavailable"),
            severity: "warning",
          };
        }
        return null;
      };
      const card = productCard();
      const cardEventId =
        card?.type === "run_status" ? card.event_id : null;
      setMessages((previous) => {
        let matched = false;
        let cardAdded = false;
        const updated = previous.map((message) => {
          if (
            message.id !== messageId &&
            !(message.role === "assistant" && message.runId === runId)
          ) {
            return message;
          }
          matched = true;
          const parts = clearAllLoadingStates(message.parts || []).filter(
            (part) =>
              !(
                part.type === "run_status" &&
                terminalRunStatus(part.event_type) === outcome
              ),
          );
          if (
            card &&
            cardEventId &&
            !cardAdded &&
            !parts.some(
              (part) =>
                part.type === "run_status" &&
                part.event_id === cardEventId,
            )
          ) {
            cardAdded = true;
            return {
              ...message,
              isStreaming: false,
              parts: [...parts, card],
            };
          }
          if (
            outcome === "cancelled" &&
            !parts.some((part) => part.type === "cancelled")
          ) {
            return {
              ...message,
              isStreaming: false,
              cancelled: true,
              parts: [...parts, { type: "cancelled" as const }],
            };
          }
          return { ...message, isStreaming: false, parts };
        });
        if (!matched && card) {
          return [
            ...updated,
            {
              id: messageId || runId,
              runId,
              role: "assistant",
              content: "",
              timestamp: new Date(),
              isStreaming: false,
              parts: [card],
            },
          ];
        }
        return updated;
      });
      return true;
    },
    [options, clearReconcileOwners],
  );

  const finalizeTerminalRun = useCallback(
    (runId: string, status: TerminalRunStatus, messageId: string): boolean =>
      convergeRunLifecycle(runId, status, messageId),
    [convergeRunLifecycle],
  );

  const finalizeRunStatusUnavailable = useCallback(
    (runId: string, messageId: string): boolean =>
      convergeRunLifecycle(runId, "status_unavailable", messageId),
    [convergeRunLifecycle],
  );

  const finalizeTerminalResultUnavailable = useCallback(
    (runId: string, messageId: string): boolean =>
      convergeRunLifecycle(runId, "terminal_result_unavailable", messageId),
    [convergeRunLifecycle],
  );

  const hydrateTerminalRun = useCallback(
    async (
      targetSessionId: string,
      targetRunId: string,
      status: TerminalRunStatus,
      fallbackMessageId: string,
    ): Promise<void> => {
      const streamVersion = streamVersionRef.current;
      const isCurrentTerminalHydration = () =>
        isMountedRef.current &&
        sessionIdRef.current === targetSessionId &&
        currentRunIdRef.current === targetRunId &&
        streamVersionRef.current === streamVersion;
      const existing = terminalHydrationOwnerRef.current;
      if (
        existing &&
        existing.sessionId === targetSessionId &&
        existing.runId === targetRunId &&
        existing.streamVersion === streamVersion
      ) {
        return existing.promise;
      }

      const owner: TerminalHydrationOwner = {
        sessionId: targetSessionId,
        runId: targetRunId,
        streamVersion,
        promise: Promise.resolve(),
      };
      const promise = (async () => {
        try {
          let timeoutId: ReturnType<typeof setTimeout> | null = null;
          const eventsData = await Promise.race([
            sessionApi.getEvents(targetSessionId, { run_id: targetRunId }),
            new Promise<never>((_resolve, reject) => {
              timeoutId = setTimeout(
                () => reject(new Error("terminal history hydration timed out")),
                TERMINAL_HISTORY_HYDRATION_TIMEOUT_MS,
              );
            }),
          ]).finally(() => {
            if (timeoutId !== null) {
              clearTimeout(timeoutId);
            }
          });
          if (!isCurrentTerminalHydration()) return;
          const events = (eventsData.events || []) as HistoryEvent[];
          let hydratedMessages = reconstructMessagesFromEvents(
            events,
            processedEventIdsRef.current,
            { options, activeSubagentStack: activeSubagentStackRef.current },
          );
          let hydratedAssistant = [...hydratedMessages]
            .reverse()
            .find(
              (message) =>
                message.role === "assistant" && message.runId === targetRunId,
            );
          if (!hydratedAssistant && status !== "cancelled") {
            finalizeTerminalResultUnavailable(targetRunId, fallbackMessageId);
            return;
          }
          if (!hydratedAssistant) {
            hydratedMessages = ensureTerminalAssistantSegment(
              hydratedMessages,
              targetRunId,
              fallbackMessageId,
            );
            hydratedAssistant = hydratedMessages.find(
              (message) =>
                message.role === "assistant" && message.runId === targetRunId,
            );
          }
          setMessages((previous) =>
            mergeHydratedRunSegment(previous, hydratedMessages, targetRunId),
          );
          finalizeTerminalRun(
            targetRunId,
            status,
            hydratedAssistant?.id || fallbackMessageId,
          );
        } catch {
          if (isCurrentTerminalHydration()) {
            finalizeTerminalResultUnavailable(targetRunId, fallbackMessageId);
          }
        } finally {
          if (terminalHydrationOwnerRef.current === owner) {
            terminalHydrationOwnerRef.current = null;
          }
        }
      })();
      owner.promise = promise;
      terminalHydrationOwnerRef.current = owner;
      return promise;
    },
    [
      options,
      finalizeTerminalRun,
      finalizeTerminalResultUnavailable,
    ],
  );

  // Create event handler context
  const createEventHandlerContext = useCallback(
    (): EventHandlerContext => ({
      options,
      sessionIdRef,
      currentRunIdRef,
      processedEventIdsRef,
      acceptedRunEventSequenceRef,
      lastHistoryTimestampRef,
      activeSubagentStackRef,
      streamVersionRef,
      setSessionId,
      setMessages,
      setConnectionStatus: (status) =>
        setConnectionStatus(status as ConnectionStatus),
      setIsInitializingSandbox,
      setSandboxError,
      onRunTerminal: finalizeTerminalRun,
      onRunStatusUnavailable: finalizeRunStatusUnavailable,
    }),
    [options, finalizeTerminalRun, finalizeRunStatusUnavailable],
  );

  // Create SSE connection context
  const createSSEContext = useCallback(
    (): SSEConnectionContext => ({
      ...createEventHandlerContext(),
      isMountedRef,
      abortControllerRef,
      isConnectingRef,
      streamingMessageIdRef,
      reconnectTimeoutRef,
      retryCountRef,
      statusRetryCountRef,
      messagesRef,
      hydrateTerminalRun,
    }),
    [createEventHandlerContext, hydrateTerminalRun],
  );

  const reconcileCurrentRun = useCallback(async () => {
    if (!isMountedRef.current) {
      return;
    }
    const targetSessionId = sessionIdRef.current;
    const targetRunId = currentRunIdRef.current;
    const streamVersion = streamVersionRef.current;
    if (!targetSessionId || !targetRunId) {
      return;
    }
    const existing = reconcileOwnerRef.current;
    if (
      existing &&
      existing.sessionId === targetSessionId &&
      existing.runId === targetRunId &&
      existing.streamVersion === streamVersion
    ) {
      return existing.promise;
    }
    const ctx = {
      ...createSSEContext(),
      sessionIdRef,
      currentRunIdRef,
      isReconnectFromHistoryRef,
    };
    const owner: ReconcileOwner = {
      sessionId: targetSessionId,
      runId: targetRunId,
      streamVersion,
      promise: Promise.resolve(),
    };
    const promise = reconnectSSE(ctx).finally(() => {
      // A scheduled retry remains the same owner until terminal/clear/switch;
      // stale completions can never clear a replacement generation's owner.
      const ownerIsCurrent =
        sessionIdRef.current === targetSessionId &&
        currentRunIdRef.current === targetRunId &&
        streamVersionRef.current === streamVersion;
      if (
        reconcileOwnerRef.current === owner &&
        (reconnectTimeoutRef.current === null || !ownerIsCurrent)
      ) {
        reconcileOwnerRef.current = null;
      }
    });
    owner.promise = promise;
    reconcileOwnerRef.current = owner;
    return promise;
  }, [createSSEContext]);

  // Cleanup on unmount
  useEffect(() => {
    isMountedRef.current = true;
    const mountedGeneration = ++mountedGenerationRef.current;
    return () => {
      if (mountedGenerationRef.current !== mountedGeneration) {
        return;
      }
      // Invalidate every asynchronous owner before releasing stream resources.
      // StrictMode creates a fresh mounted generation immediately afterwards.
      isMountedRef.current = false;
      mountedGenerationRef.current += 1;
      historyLoadTokenRef.current += 1;
      invalidateRunControl();
      sessionGenerationRef.current += 1;
      submissionTokenRef.current += 1;
      activePreAdmissionSubmissionRef.current = null;
      streamVersionRef.current += 1;
      statusRetryCountRef.current = 0;
      acceptedRunEventSequenceRef.current = {
        sessionId: null,
        runId: null,
        sequence: null,
      };
      isLoadingHistoryRef.current = false;
      isSendingRef.current = false;
      isConnectingRef.current = false;
      retryCountRef.current = 0;
      statusRetryCountRef.current = 0;
      streamingMessageIdRef.current = null;
      isReconnectFromHistoryRef.current = false;
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
        abortControllerRef.current = null;
      }
      clearReconcileOwners();
      clearReconnectTimeout(reconnectTimeoutRef);
    };
  }, [clearReconcileOwners, invalidateRunControl]);

  // Load message history from backend
  const loadHistory = useCallback(
    async (
      targetSessionId: string,
      targetRunId?: string,
      runControlAdoption?: { owner: RunControlOwner },
    ) => {
      if (!isMountedRef.current) {
        return null;
      }
      const preservesRunControlParent =
        runControlAdoption !== undefined &&
        runControlLifecycle.isCurrentOwner(runControlAdoption.owner);
      if (runControlAdoption && !preservesRunControlParent) {
        return null;
      }
      const mountedGeneration = mountedGenerationRef.current;
      if (isLoadingHistoryRef.current) {
        console.log(
          "[loadHistory] Switching to new session, aborting previous load...",
        );
      }
      const historyLoadToken = beginHistoryLoad(historyLoadTokenRef);
      invalidateRunControl({ preserveParent: preservesRunControlParent });
      const previousSessionId = sessionIdRef.current;
      const previousRunId = currentRunIdRef.current;
      if (previousSessionId !== targetSessionId) {
        // A new session owns an independent transport-reconnect budget. Clear
        // the previous session's budget before asynchronous history work.
        retryCountRef.current = 0;
        acceptedRunEventSequenceRef.current = {
          sessionId: null,
          runId: null,
          sequence: null,
        };
      }
      const isCurrentHistoryLoadRequest = () =>
        isMountedRef.current &&
        mountedGenerationRef.current === mountedGeneration &&
        isCurrentHistoryLoad(historyLoadTokenRef, historyLoadToken);
      handoffActivePreAdmissionSubmission({ projectMessages: false });
      sessionGenerationRef.current += 1;
      submissionTokenRef.current += 1;
      streamVersionRef.current += 1;
      clearReconcileOwners();
      isSendingRef.current = false;
      statusRetryCountRef.current = 0;

      isLoadingHistoryRef.current = true;
      setIsLoadingHistory(true);

      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
        abortControllerRef.current = null;
      }
      isConnectingRef.current = false;
      streamingMessageIdRef.current = null;
      clearReconnectTimeout(reconnectTimeoutRef);

      setIsLoading(true);
      setMessages([]);
      setError(null);
      setCurrentRunId(null);
      currentRunIdRef.current = null;

      processedEventIdsRef.current.clear();
      lastHistoryTimestampRef.current = null;
      const markReadPromise = sessionApi
        .markRead(targetSessionId)
        .catch(() => {});

      // Clear approvals before loading new session
      options?.onClearApprovals?.();

      try {
        await markReadPromise;
        if (!isCurrentHistoryLoadRequest()) {
          return null;
        }

        const sessionData = await sessionApi.get(targetSessionId);
        if (!isCurrentHistoryLoadRequest()) {
          return null;
        }

        if (sessionData) {
          sessionIdRef.current = targetSessionId;
          setSessionId(targetSessionId);
          const loadedAgentId = sessionData.agent_id || DEFAULT_CHAT_AGENT_ID;
          sessionAgentIdRef.current = loadedAgentId;
          setSessionAgentId(loadedAgentId);

          // 从 metadata 提取配置信息
          const sessionConfig = {
            agent_options:
              (sessionData.metadata?.agent_options as Record<
                string,
                boolean | string | number
              >) || undefined,
            disabled_tools:
              (sessionData.metadata?.disabled_tools as string[]) || undefined,
            disabled_skills:
              (sessionData.metadata?.disabled_skills as string[]) || undefined,
            disabled_mcp_tools:
              (sessionData.metadata?.disabled_mcp_tools as string[]) ||
              undefined,
          };

          // Event history determines the exact latest run before its status is
          // queried. Session metadata can be absent or stale in production.
          const eventsPromise = sessionApi.getEvents(
            targetSessionId,
            targetRunId ? { run_id: targetRunId } : undefined,
          );
          const feedbackPromise = canReadFeedback
            ? feedbackApi
                .list(0, 100, undefined, undefined, targetSessionId)
                .catch(() => {
                  console.warn("[loadHistory] Failed to load feedback");
                  return null;
                })
            : Promise.resolve(null);

          const [eventsData, feedbackList] = await Promise.all([
            eventsPromise,
            feedbackPromise,
          ]);
          if (!isCurrentHistoryLoadRequest()) {
            return null;
          }

          const historyCurrentRunId = resolveHistoryCurrentRunId({
            targetRunId,
            sessionData,
            eventsData,
          });
          const historySequence = maxAcceptedRunEventSequence(
            eventsData.events,
            historyCurrentRunId,
          );
          const acceptedProgress = acceptedRunEventSequenceRef.current;
          if (
            historyCurrentRunId &&
            acceptedProgress.sessionId === targetSessionId &&
            acceptedProgress.runId === historyCurrentRunId
          ) {
            if (
              historySequence !== null &&
              (acceptedProgress.sequence === null ||
                historySequence > acceptedProgress.sequence)
            ) {
              acceptedRunEventSequenceRef.current = {
                ...acceptedProgress,
                sequence: historySequence,
              };
            }
          } else {
            acceptedRunEventSequenceRef.current = {
              sessionId: historyCurrentRunId ? targetSessionId : null,
              runId: historyCurrentRunId,
              sequence: historySequence,
            };
          }
          if (
            previousSessionId === targetSessionId &&
            previousRunId !== historyCurrentRunId
          ) {
            // A reload of the same active run must retain its bounded
            // transport budget; only a true session/run handoff starts over.
            retryCountRef.current = 0;
          }
          const statusResult = historyCurrentRunId
            ? await queryAuthoritativeRunStatus({
                sessionId: targetSessionId,
                runId: historyCurrentRunId,
                isCurrent: isCurrentHistoryLoadRequest,
                statusRetryCountRef,
              })
            : null;
          if (!isCurrentHistoryLoadRequest()) {
            return null;
          }

          // Reconnect only after the authoritative run record says it is
          // active. Event history alone must never revive a failed run.
          const normalizedStatus =
            statusResult?.kind === "resolved" ? statusResult.status : null;
          const isTaskRunning = Boolean(
            normalizedStatus && isActiveRunStatus(normalizedStatus),
          );
          // A persisted cancel request keeps the run observable until the
          // server publishes its terminal result, but it must never cause a
          // page reload to reconnect an active generation. The next explicit
          // history/status read is GET-only and remains the convergence path.
          const isCancelRequested = normalizedStatus === "cancel_requested";
          const terminalStatus = terminalRunStatus(normalizedStatus);
          const statusUnavailable = statusResult?.kind === "unavailable";
          const activeHistoryRunId =
            isTaskRunning && historyCurrentRunId ? historyCurrentRunId : null;
          let reconstructedMessages = eventsData.events?.length
            ? reconstructMessagesFromEvents(
                eventsData.events as HistoryEvent[],
                processedEventIdsRef.current,
                { options, activeSubagentStack: activeSubagentStackRef.current },
              )
            : [];
          if (targetRunId) {
            reconstructedMessages = mergeHydratedRunSegment(
              [],
              reconstructedMessages,
              targetRunId,
            );
          }

          if (feedbackList && feedbackList.items.length > 0) {
            const feedbackMap = new Map(
              feedbackList.items.map((f) => [
                f.run_id,
                { feedback: f.rating, feedbackId: f.id },
              ]),
            );
            reconstructedMessages = reconstructedMessages.map((msg) => {
              const feedbackInfo = msg.runId
                ? feedbackMap.get(msg.runId)
                : undefined;
              return feedbackInfo
                ? { ...msg, ...feedbackInfo }
                : msg;
            });
          }

          const lastTimestamp = getLastEventTimestamp(
            (eventsData.events || []) as HistoryEvent[],
          );
          if (lastTimestamp) {
            lastHistoryTimestampRef.current = lastTimestamp;
          }

          let streamingMessageId: string | null = null;
          if (isTaskRunning && historyCurrentRunId) {
            const prepared = prepareMessagesForRunningRun(
              reconstructedMessages,
              historyCurrentRunId,
              () => uuid(),
            );
            reconstructedMessages = prepared.messages;
            streamingMessageId = prepared.streamingMessageId;
          }
          messagesRef.current = reconstructedMessages;
          setMessages(reconstructedMessages);

          // A restored active or terminal run becomes the authoritative parent
          // for the persistent playback lifecycle. The lifecycle itself owns
          // its snapshot; this only binds the parent identity before any panel
          // read or child adoption can publish.
          if (historyCurrentRunId) {
            bindRunControlParent(targetSessionId, historyCurrentRunId);
          }

          const historyMessageId = historyCurrentRunId
            ? ([...reconstructedMessages]
                .reverse()
                .find(
                  (message) =>
                    message.runId === historyCurrentRunId &&
                    message.role === "assistant",
                )?.id || historyCurrentRunId)
            : null;

          if (statusUnavailable && historyCurrentRunId) {
            currentRunIdRef.current = historyCurrentRunId;
            setCurrentRunId(historyCurrentRunId);
            finalizeRunStatusUnavailable(
              historyCurrentRunId,
              historyMessageId || historyCurrentRunId,
            );
          } else if (terminalStatus && historyCurrentRunId) {
            // An explicit target already arrived through the exact history
            // contract. Default history still hydrates that exact run before
            // terminal convergence can clear presentation.
            currentRunIdRef.current = historyCurrentRunId;
            setCurrentRunId(historyCurrentRunId);
            if (targetRunId) {
              let exactAssistant = reconstructedMessages.find(
                (message) =>
                  message.role === "assistant" &&
                  message.runId === historyCurrentRunId,
              );
              if (!exactAssistant && terminalStatus !== "cancelled") {
                finalizeTerminalResultUnavailable(
                  historyCurrentRunId,
                  historyMessageId || historyCurrentRunId,
                );
              } else {
                if (!exactAssistant) {
                  reconstructedMessages = ensureTerminalAssistantSegment(
                    reconstructedMessages,
                    historyCurrentRunId,
                    historyMessageId || historyCurrentRunId,
                  );
                  messagesRef.current = reconstructedMessages;
                  setMessages(reconstructedMessages);
                  exactAssistant = reconstructedMessages.find(
                    (message) =>
                      message.role === "assistant" &&
                      message.runId === historyCurrentRunId,
                  );
                }
                finalizeTerminalRun(
                  historyCurrentRunId,
                  terminalStatus,
                  exactAssistant?.id || historyMessageId || historyCurrentRunId,
                );
              }
            } else {
              await hydrateTerminalRun(
                targetSessionId,
                historyCurrentRunId,
                terminalStatus,
                historyMessageId || historyCurrentRunId,
              );
            }
          } else {
            setCurrentRunId(activeHistoryRunId);
            currentRunIdRef.current = activeHistoryRunId;
          }

          if (
            isTaskRunning &&
            !isCancelRequested &&
            historyCurrentRunId &&
            streamingMessageId
          ) {
            isReconnectFromHistoryRef.current = false;
            const ctx = createSSEContext();
            connectToSSE(
              targetSessionId,
              historyCurrentRunId,
              streamingMessageId,
              ctx,
            ).catch((streamError) => {
              if (
                !isCurrentHistoryLoadRequest() ||
                currentRunIdRef.current !== historyCurrentRunId
              ) {
                return;
              }
              if (isNonRetryableSSEAuthenticationError(streamError)) {
                finalizeRunStatusUnavailable(
                  historyCurrentRunId,
                  streamingMessageId,
                );
                return;
              }
              reconcileCurrentRun().catch(() => {
                console.warn("[loadHistory] SSE reconciliation failed");
              });
            });
          }

          // Return sessionConfig *before* any SSE reconnect so that the
          // caller can immediately restore model selection / agent / config.

          return sessionConfig;
        }
      } catch {
        if (isCurrentHistoryLoadRequest()) {
          console.error("[loadHistory] Failed to load session");
          setError(i18n.t("chat.requestFailed"));
        }
      } finally {
        if (isCurrentHistoryLoadRequest()) {
          setIsLoading(false);
          setIsLoadingHistory(false);
          isLoadingHistoryRef.current = false;
        }
      }

      return null;
    },
    [
      options,
      createSSEContext,
      canReadFeedback,
      finalizeRunStatusUnavailable,
      finalizeTerminalResultUnavailable,
      finalizeTerminalRun,
      hydrateTerminalRun,
      reconcileCurrentRun,
      clearReconcileOwners,
      bindRunControlParent,
      handoffActivePreAdmissionSubmission,
      invalidateRunControl,
      runControlLifecycle,
    ],
  );

  const adoptRunControlChild = useCallback(
    async (
      owner: RunControlOwner,
      child: RunControlChild,
    ): Promise<"adopted" | "created_unopened" | "superseded"> => {
      if (!runControlLifecycle.isCurrentOwner(owner)) {
        return "superseded";
      }
      const parentBefore = runControlParentRef.current;
      if (
        !parentBefore ||
        parentBefore.sessionId !== owner.sessionId ||
        parentBefore.runId !== owner.runId
      ) {
        return "superseded";
      }

      // This is the only child-adoption call site. It invokes the existing
      // history loader exactly once and never lets a panel write transcript or
      // route state directly.
      const restored = await loadHistory(child.sessionId, child.runId, { owner });
      if (restored !== null) {
        const currentParent = runControlParentRef.current;
        if (
          currentParent?.sessionId === child.sessionId &&
          currentParent.runId === child.runId
        ) {
          runControlLifecycle.open();
          return "adopted";
        }
        return "superseded";
      }

      // A null/rejected history read says only that the child was created but
      // could not be opened. Keep its opaque IDs for an explicit GET-only
      // reopen, unless a newer session/auth/history owner replaced this parent.
      const currentParent = currentRunControlParent();
      if (
        !currentParent ||
        currentParent.sessionId !== owner.sessionId ||
        currentParent.runId !== owner.runId ||
        currentParent.authRevision !== owner.authRevision
      ) {
        return "superseded";
      }
      runControlLifecycle.retainCreatedUnopened(currentParent, child);
      return "created_unopened";
    },
    [currentRunControlParent, loadHistory, runControlLifecycle],
  );

  const reconnectRunControlOwner = useCallback(
    async (owner: RunControlOwner): Promise<void> => {
      if (!runControlLifecycle.isCurrentOwner(owner)) return;
      if (
        sessionIdRef.current !== owner.sessionId ||
        currentRunIdRef.current !== owner.runId
      ) {
        return;
      }
      await reconcileCurrentRun();
    },
    [reconcileCurrentRun, runControlLifecycle],
  );

  useLayoutEffect(() => {
    runControlLifecycle.configure({
      adoptRunControlChild,
      reconnectRunControlOwner,
    });
  }, [adoptRunControlChild, reconnectRunControlOwner, runControlLifecycle]);

  // Send message
  const sendMessage = useCallback(
    async (
      content: string,
      agentOptions?: Record<string, boolean | string | number>,
      attachments?: MessageAttachment[],
      selectedSkill?: SelectedSkillRequest | null,
    ): Promise<SubmissionOutcome> => {
      if (!isMountedRef.current) return { status: "failed" };
      if (!content.trim()) return { status: "failed" };

      if (submissionAuthIncarnationFenceRef.current !== null) {
        return { status: "failed" };
      }

      if (submissionUncertaintyRef.current !== null) {
        const statusUnavailable = i18n.t("chat.runTerminal.statusUnavailable", {
          defaultValue: i18n.t("chat.requestFailed"),
        });
        setError(statusUnavailable);
        toast.error(statusUnavailable);
        return { status: "failed" };
      }

      if (isSendingRef.current) {
        console.log(
          "[sendMessage] Already sending, ignoring duplicate request",
        );
        return { status: "failed" };
      }
      // A new user submission replaces the parent run before it can mutate
      // optimistic transcript state or issue its POST. This fences a pending
      // retry/resume owner from starting while the next chat admission is open.
      invalidateRunControl();
      isSendingRef.current = true;
      const submissionToken = ++submissionTokenRef.current;
      const mountedGeneration = mountedGenerationRef.current;
      const requestSessionGeneration = sessionGenerationRef.current;
      const requestSessionId = sessionIdRef.current;
      const requestAgentId = sessionAgentIdRef.current;
      streamVersionRef.current += 1;
      clearReconcileOwners();
      statusRetryCountRef.current = 0;
      const isCurrentSubmission = () =>
        isMountedRef.current &&
        mountedGenerationRef.current === mountedGeneration &&
        submissionTokenRef.current === submissionToken &&
        sessionGenerationRef.current === requestSessionGeneration;
      const isCurrentRequestSession = () =>
        isCurrentSubmission() && sessionIdRef.current === requestSessionId;
      const finishCurrentSubmission = () => {
        if (isCurrentSubmission()) {
          isSendingRef.current = false;
        }
      };

      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
        abortControllerRef.current = null;
      }
      isConnectingRef.current = false;
      clearReconnectTimeout(reconnectTimeoutRef);

      processedEventIdsRef.current.clear();
      acceptedRunEventSequenceRef.current = {
        sessionId: null,
        runId: null,
        sequence: null,
      };
      lastHistoryTimestampRef.current = null;

      const previousMessages = messagesRef.current;
      const submissionOwner = authScopeRef.current;
      if (submissionOwner === null) {
        finishCurrentSubmission();
        return { status: "failed" };
      }
      const submissionId = uuid();
      const persistedSubmission = {
        version: 1,
        owner: submissionOwner,
        submissionId,
      } as const;
      if (!persistSubmissionReference(persistedSubmission)) {
        // A durable key is the only recovery authority for an unknown POST.
        // Do not send if the browser cannot prove that it retained the key.
        const statusUnavailable = i18n.t("chat.runTerminal.statusUnavailable", {
          defaultValue: i18n.t("chat.requestFailed"),
        });
        setError(statusUnavailable);
        toast.error(statusUnavailable);
        finishCurrentSubmission();
        return { status: "failed" };
      }
      activePreAdmissionSubmissionRef.current = {
        owner: submissionOwner,
        submissionId,
        sessionId: requestSessionId,
        previousMessages,
        token: submissionToken,
      };
      if (confirmationRecovery !== null) {
        setConfirmationRecovery(null);
      }
      const {
        messages: optimisticMessages,
        userMessageId,
        assistantMessageId,
      } =
        createOptimisticMessagesForSend({
          previousMessages,
          content,
          attachments,
        });

      messagesRef.current = optimisticMessages;
      setMessages(optimisticMessages);
      setIsLoading(true);
      setError(null);
      let finalAssistantMessageId = assistantMessageId;
      let admissionAccepted = false;

      try {
        // 用户发送消息时标记当前 session 为已读
        if (requestSessionId) {
          sessionApi.markRead(requestSessionId).catch(() => {});
        }

        // 获取当前禁用的 skills 和 mcp_tools
        const disabledSkills = options?.getDisabledSkills?.() || [];
        const disabledMcpTools = options?.getDisabledMcpTools?.() || [];

        // Merge session-level agent options (e.g. model) with ChatInput values
        const fullAgentOptions = {
          ...options?.getAgentOptions?.(),
          ...agentOptions,
        };

        // Option getters are application extension seams. A getter can
        // synchronously publish an auth-incarnation event, so validate the
        // same ownership facts immediately before the irreversible POST.
        if (
          submissionTokenRef.current !== submissionToken ||
          submissionAuthIncarnationFenceRef.current !== null ||
          authScopeRef.current !== submissionOwner
        ) {
          handoffActivePreAdmissionSubmission({
            expectedToken: submissionToken,
            requireCurrentToken: true,
          });
          setConnectionStatus("disconnected");
          setIsInitializingSandbox(false);
          setIsLoading(false);
          if (submissionTokenRef.current === submissionToken) {
            isSendingRef.current = false;
          }
          return { status: "failed" };
        }

        const submitData: ChatStreamResponse = await sessionApi.submitChat(
          content,
          requestSessionId ?? undefined,
          fullAgentOptions,
          attachments,
          disabledSkills,
          disabledMcpTools,
          selectedSkill,
          submissionId,
          requestAgentId,
        );

        if (!isCurrentRequestSession()) {
          handoffActivePreAdmissionSubmission({
            expectedToken: submissionToken,
            requireCurrentToken: true,
          });
          return { status: "failed" };
        }

        // An old backend silently ignores the optional key. A concrete legacy
        // response can still drive its existing session/run lifecycle, but it
        // never enables submission resolver/retry recovery; transport loss
        // remains fail-closed because the persisted key cannot resolve.
        const protocolEchoed = submitData.submission_id === submissionId;

        if (isChatStreamNeedsConfirmation(submitData)) {
          clearActivePreAdmissionSubmission(submissionToken);
          const confirmationMessages = projectOwnedConfirmationMessages(
            messagesRef.current,
            submitData.suggestions,
            assistantMessageId,
          );
          if (confirmationMessages) {
            messagesRef.current = confirmationMessages;
            setMessages(confirmationMessages);
            setConfirmationRecovery(null);
          } else {
            setConfirmationRecovery({
              owner: submissionOwner,
              submissionId,
              suggestions: submitData.suggestions,
            });
          }
          submissionUncertaintyRef.current = null;
          setPendingSubmissionId(null);
          setError(null);
          removePersistedSubmissionReference(submissionOwner, submissionId);
          setConnectionStatus("disconnected");
          setIsInitializingSandbox(false);
          setIsLoading(false);
          finishCurrentSubmission();
          return { status: "accepted" };
        }

        if (submitData.status === "accepted_pending_enqueue") {
          if (!protocolEchoed) {
            throw new Error("chat_submission_protocol_unavailable");
          }
          const statusUnavailable = i18n.t("chat.runTerminal.statusUnavailable", {
            defaultValue: i18n.t("chat.requestFailed"),
          });
          const pendingMessages = messagesRef.current.filter(
            (message) => message.id !== assistantMessageId,
          );
          messagesRef.current = pendingMessages;
          setMessages(pendingMessages);
          if (!requestSessionId && submitData.session_id) {
            sessionIdRef.current = submitData.session_id;
            setSessionId(submitData.session_id);
          }
          submissionUncertaintyRef.current = {
            sessionId: submitData.session_id || requestSessionId,
            submissionId,
            owner: submissionOwner,
            previousMessages,
          };
          setPendingSubmissionId(submissionId);
          clearActivePreAdmissionSubmission(submissionToken);
          setError(statusUnavailable);
          toast.error(statusUnavailable);
          setConnectionStatus("disconnected");
          setIsInitializingSandbox(false);
          setIsLoading(false);
          finishCurrentSubmission();
          return { status: "failed" };
        }

        const newSessionId = submitData.session_id;
        const newRunId = submitData.run_id;
        const routedAgentId = resolveChatSessionAgentId(
          submitData,
          requestAgentId,
        );
        sessionAgentIdRef.current = routedAgentId;
        setSessionAgentId(routedAgentId);

        // Handle queued status — show toast and wait via SSE
        if (submitData.status === "queued") {
          toast.loading(
            i18n.t("chat.queued", { position: submitData.queue_position }),
            { id: "chat-queue", duration: Infinity },
          );
        }

        if (!requestSessionId && newSessionId) {
          sessionIdRef.current = newSessionId;
          setSessionId(newSessionId);
          const now = new Date().toISOString();

          // 构建完整的对话配置
          const conversationConfig: Record<string, unknown> = {
            current_run_id: newRunId,
            agent_id: routedAgentId,
            agent_options: fullAgentOptions,
            disabled_skills: disabledSkills,
            disabled_mcp_tools: disabledMcpTools,
          };
          const newSession: BackendSession = {
            id: newSessionId,
            agent_id: routedAgentId,
            created_at: now,
            updated_at: now,
            is_active: true,
            metadata: conversationConfig,
          };
          setNewlyCreatedSession(newSession);

          sessionApi
            .generateTitle(newSessionId, content, i18n.language)
            .then((result) => {
              if (
                !isMountedRef.current ||
                mountedGenerationRef.current !== mountedGeneration ||
                sessionGenerationRef.current !== requestSessionGeneration ||
                sessionIdRef.current !== newSessionId
              ) {
                return;
              }
              setNewlyCreatedSession((prev) =>
                prev?.id === newSessionId
                  ? {
                      ...prev,
                      name: result.title,
                      updated_at: new Date().toISOString(),
                    }
                  : prev,
              );
              dispatchSessionTitleUpdated({
                sessionId: newSessionId,
                title: result.title,
              });
            })
            .catch(() => {
              console.warn("[sendMessage] Failed to generate title");
            });
        } else if (requestSessionId && newRunId) {
          // 更新现有 session 的 metadata
          const conversationConfig: Record<string, unknown> = {
            ...((newlyCreatedSession?.metadata as Record<string, unknown>) ||
              {}),
            current_run_id: newRunId,
            agent_id: routedAgentId,
            agent_options: fullAgentOptions,
            disabled_skills: disabledSkills,
            disabled_mcp_tools: disabledMcpTools,
          };

          setNewlyCreatedSession((prev) =>
            prev
              ? {
                  ...prev,
                  agent_id: routedAgentId,
                  metadata: conversationConfig,
                  updated_at: new Date().toISOString(),
                }
              : null,
          );
        }
        if (newRunId) {
          // A confirmed run owns a fresh continuous transport-recovery budget.
          retryCountRef.current = 0;
          acceptedRunEventSequenceRef.current = {
            sessionId: newSessionId || requestSessionId || null,
            runId: newRunId,
            sequence: null,
          };
          setCurrentRunId(newRunId);
          currentRunIdRef.current = newRunId;
          const runControlSessionId = newSessionId || requestSessionId;
          if (runControlSessionId) {
            bindRunControlParent(runControlSessionId, newRunId);
          }
          setMessages((prev) => {
            const nextMessages = prev.map((m) =>
              m.id === userMessageId
                ? { ...m, runId: newRunId }
                : m.id === assistantMessageId
                ? {
                    ...m,
                    id: newRunId,
                    runId: newRunId,
                  }
                : m,
            );
            messagesRef.current = nextMessages;
            return nextMessages;
          });
        }

        const streamSessionId = newSessionId || requestSessionId;
        const streamRunId = newRunId;
        finalAssistantMessageId = newRunId || assistantMessageId;

        if (!streamSessionId || !streamRunId) {
          throw new Error("Missing session_id or run_id");
        }
        admissionAccepted = true;
        clearActivePreAdmissionSubmission(submissionToken);
        removePersistedSubmissionReference(submissionOwner, submissionId);
        setPendingSubmissionId(null);

        isReconnectFromHistoryRef.current = false;
        const ctx = createSSEContext();
        void connectToSSE(
          streamSessionId,
          streamRunId,
          finalAssistantMessageId,
          ctx,
        )
          .catch(async (streamError) => {
            if (
              !isCurrentSubmission() ||
              currentRunIdRef.current !== streamRunId
            ) {
              return;
            }
            // Admission has reached a concrete stream owner; a failed setup
            // must never leave the queued toast visible during reconciliation.
            toast.dismiss("chat-queue");
            if (isNonRetryableSSEAuthenticationError(streamError)) {
              finalizeRunStatusUnavailable(
                streamRunId,
                finalAssistantMessageId,
              );
              return;
            }
            await reconcileCurrentRun();
          })
          .finally(() => {
            if (isCurrentSubmission()) {
              finishCurrentSubmission();
              if (currentRunIdRef.current !== streamRunId) {
                setIsLoading(false);
              }
            }
          });
        return { status: "accepted" };
      } catch (err) {
        if (!isCurrentRequestSession()) {
          handoffActivePreAdmissionSubmission({
            expectedToken: submissionToken,
            requireCurrentToken: true,
          });
          return { status: "failed" };
        }
        toast.dismiss("chat-queue");
        const prePersistenceRejection =
          !admissionAccepted && isProvenPrePersistenceChatRejection(err);
        if (prePersistenceRejection) {
          clearActivePreAdmissionSubmission(submissionToken);
          removePersistedSubmissionReference(submissionOwner, submissionId);
          setPendingSubmissionId(null);
          messagesRef.current = previousMessages;
          setMessages(previousMessages);
          const recoverableCode = selectedSkill
            ? getSelectedSkillRecoverableCode(err)
            : null;
          if (recoverableCode) {
            setConnectionStatus("disconnected");
            setIsInitializingSandbox(false);
            setIsLoading(false);
            finishCurrentSubmission();
            return { status: "recoverable_error", code: recoverableCode };
          }
          const errorMessage = formatChatSubmissionError(err);
          setError(errorMessage);
          toast.error(errorMessage);
        } else if (!admissionAccepted) {
          const statusUnavailable = i18n.t("chat.runTerminal.statusUnavailable", {
            defaultValue: i18n.t("chat.requestFailed"),
          });
          const uncertainMessages = optimisticMessages.filter(
            (message) => message.id !== assistantMessageId,
          );
          // The request may have committed before its response was lost. Keep
          // its user turn without projecting an invented assistant result, and
          // block another mutation until history/status reconciliation.
          handoffActivePreAdmissionSubmission({
            expectedToken: submissionToken,
            messages: uncertainMessages,
            requireCurrentToken: true,
          });
          setError(statusUnavailable);
          toast.error(statusUnavailable);
        } else {
          const errorMessage = formatChatSubmissionError(err);
          setError(errorMessage);
          setMessages((prev) =>
            prev.map((m) =>
              m.id === finalAssistantMessageId
                ? {
                    ...m,
                    content: i18n.t("chat.errorPrefix", { error: errorMessage }),
                    isStreaming: false,
                    parts: clearAllLoadingStates(m.parts || []),
                  }
                : m,
            ),
          );
        }
        setConnectionStatus("disconnected");
        setIsInitializingSandbox(false);
        setIsLoading(false);
        finishCurrentSubmission();
        return { status: "failed" };
      }
    },
    [
      createSSEContext,
      newlyCreatedSession?.metadata,
      options,
      finalizeRunStatusUnavailable,
      reconcileCurrentRun,
      clearReconcileOwners,
      clearActivePreAdmissionSubmission,
      confirmationRecovery,
      bindRunControlParent,
      handoffActivePreAdmissionSubmission,
      invalidateRunControl,
    ],
  );

  const stopGeneration = useCallback(async () => {
    const currentRunId = currentRunIdRef.current;
    const currentSessionId = sessionIdRef.current;
    if (!currentRunId || !currentSessionId) {
      return;
    }
    const owner = runControlLifecycle.getSnapshot().owner;
    if (
      !owner ||
      owner.sessionId !== currentSessionId ||
      owner.runId !== currentRunId ||
      !runControlLifecycle.isCurrentOwner(owner)
    ) {
      bindRunControlParent(currentSessionId, currentRunId);
    }
    // A cancel acknowledgement is only a request. The existing SSE/reconcile
    // path remains the sole terminal convergence writer for the transcript.
    await runControlLifecycle.cancel();
  }, [bindRunControlParent, runControlLifecycle]);

  const clearMessages = useCallback(() => {
    // Invalidate every asynchronous owner before clearing React state so a
    // delayed submit or history restore cannot repopulate this blank session.
    historyLoadTokenRef.current += 1;
    invalidateRunControl();
    handoffActivePreAdmissionSubmission({ projectMessages: false });
    sessionGenerationRef.current += 1;
    submissionTokenRef.current += 1;
    streamVersionRef.current += 1;
    isLoadingHistoryRef.current = false;
    isSendingRef.current = false;
    isConnectingRef.current = false;
    retryCountRef.current = 0;
    statusRetryCountRef.current = 0;
    clearReconcileOwners();
    setMessages([]);
    setConfirmationRecovery(null);
    setSessionId(null);
    sessionAgentIdRef.current = DEFAULT_CHAT_AGENT_ID;
    setSessionAgentId(DEFAULT_CHAT_AGENT_ID);
    setError(null);
    setCurrentRunId(null);
    setNewlyCreatedSession(null);
    setIsLoading(false);
    setIsLoadingHistory(false);
    setIsInitializingSandbox(false);
    setSandboxError(null);
    setConnectionStatus("disconnected");
    processedEventIdsRef.current.clear();
    acceptedRunEventSequenceRef.current = {
      sessionId: null,
      runId: null,
      sequence: null,
    };
    lastHistoryTimestampRef.current = null;
    streamingMessageIdRef.current = null;
    isReconnectFromHistoryRef.current = false;
    messagesRef.current = [];
    sessionIdRef.current = null;
    currentRunIdRef.current = null;
    activeSubagentStackRef.current = [];
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    clearReconnectTimeout(reconnectTimeoutRef);
    toast.dismiss("chat-queue");
  }, [
    clearReconcileOwners,
    handoffActivePreAdmissionSubmission,
    invalidateRunControl,
  ]);

  useLayoutEffect(() => {
    if (runControlAuthIdentityRef.current === runControlAuthIdentity) {
      return;
    }
    // Cross-tab storage handling updates AuthProvider state, while the local
    // production event has already fenced same-tab changes synchronously.
    runControlAuthIdentityRef.current = runControlAuthIdentity;
    if (
      appliedRunControlAuthEventRevisionRef.current !==
      runControlAuthEventRevisionRef.current
    ) {
      appliedRunControlAuthEventRevisionRef.current =
        runControlAuthEventRevisionRef.current;
      return;
    }
    runControlAuthRevisionRef.current += 1;
    invalidateRunControl();
  }, [invalidateRunControl, runControlAuthIdentity]);

  const authScopeAuthenticated = isAuthenticated && Boolean(user);
  const authScopeTenantId = user?.tenant_id ?? "";
  const authScopeUserId = user?.id ?? "";
  const authScope = useMemo<AuthScope | null>(
    () =>
      authScopeAuthenticated
        ? [authScopeTenantId, authScopeUserId]
        : null,
    [authScopeAuthenticated, authScopeTenantId, authScopeUserId],
  );

  const installPersistedSubmissionFence = useCallback(
    (owner: AuthScope): PersistedSubmissionReference | null => {
      const persisted = readPersistedSubmissionReferences().find((candidate) =>
        authScopesEqual(candidate.owner, owner),
      );
      if (!persisted) {
        submissionUncertaintyRef.current = null;
        setPendingSubmissionId(null);
        return null;
      }
      const current = submissionUncertaintyRef.current;
      submissionUncertaintyRef.current = {
        sessionId:
          current &&
          authScopesEqual(current.owner, owner) &&
          current.submissionId === persisted.submissionId
            ? current.sessionId
            : null,
        submissionId: persisted.submissionId,
        owner,
        previousMessages:
          current &&
          authScopesEqual(current.owner, owner) &&
          current.submissionId === persisted.submissionId
            ? current.previousMessages
            : undefined,
        suppressMessageProjection:
          current &&
          authScopesEqual(current.owner, owner) &&
          current.submissionId === persisted.submissionId
            ? current.suppressMessageProjection
            : undefined,
      };
      setPendingSubmissionId(persisted.submissionId);
      return persisted;
    },
    [],
  );

  const advancePersistedSubmissionFence = useCallback(
    (owner: AuthScope, submissionId: string): PersistedSubmissionReference | null => {
      removePersistedSubmissionReference(owner, submissionId);
      return installPersistedSubmissionFence(owner);
    },
    [installPersistedSubmissionFence],
  );

  useLayoutEffect(() => {
    const fencedIncarnation = submissionAuthIncarnationFenceRef.current;
    if (
      fencedIncarnation === null ||
      fencedIncarnation !== browserAuthIncarnation ||
      isAuthLoading ||
      (isAuthenticated && authScope === null)
    ) {
      return;
    }
    // Hydration has now settled on this incarnation. Reinstall only that
    // principal's durable reference before passive resolver work may begin.
    submissionAuthIncarnationFenceRef.current = null;
    if (authScope !== null && installPersistedSubmissionFence(authScope)) {
      setError(
        i18n.t("chat.runTerminal.statusUnavailable", {
          defaultValue: i18n.t("chat.requestFailed"),
        }),
      );
    }
  }, [
    authScope,
    browserAuthIncarnation,
    installPersistedSubmissionFence,
    isAuthenticated,
    isAuthLoading,
  ]);

  useLayoutEffect(() => {
    const previousAuthScope = authScopeRef.current;
    if (authScopesEqual(previousAuthScope, authScope)) {
      return;
    }
    // An A→B→A transition must be distinguishable from the original A. Every
    // resolver and retry captures this epoch before it crosses an await.
    authScopeGenerationRef.current += 1;
    submissionResolverOwnerRef.current = null;
    authScopeRef.current = authScope;

    // The first authenticated hydration has no prior chat owner. Every later
    // identity replacement or logout must invalidate the same fences as an
    // explicit new-chat action before it can publish stale session state.
    if (previousAuthScope !== null || authScope === null) {
      clearMessages();
    }
    if (authScope === null) {
      submissionUncertaintyRef.current = null;
      setPendingSubmissionId(null);
      return;
    }
    // Read and install durable uncertainty in the layout phase. The composer
    // sees this synchronous ref fence before any passive resolver GET starts.
    if (installPersistedSubmissionFence(authScope)) {
      setError(
        i18n.t("chat.runTerminal.statusUnavailable", {
          defaultValue: i18n.t("chat.requestFailed"),
        }),
      );
    }
  }, [authScope, clearMessages, installPersistedSubmissionFence]);

  const resolvePersistedSubmission = useCallback(
    async (owner: AuthScope, submissionId: string) => {
      const pending = submissionUncertaintyRef.current;
      if (
        !pending ||
        submissionAuthIncarnationFenceRef.current !== null ||
        pending.submissionId !== submissionId ||
        !authScopesEqual(pending.owner, owner) ||
        !authScopesEqual(authScopeRef.current, owner)
      ) {
        return;
      }
      const authScopeGeneration = authScopeGenerationRef.current;
      const resolverSessionGeneration = sessionGenerationRef.current;
      const isCurrentResolution = (expectedSessionGeneration = resolverSessionGeneration) =>
        isMountedRef.current &&
        authScopeGenerationRef.current === authScopeGeneration &&
        submissionAuthIncarnationFenceRef.current === null &&
        sessionGenerationRef.current === expectedSessionGeneration &&
        authScopesEqual(authScopeRef.current, owner) &&
        submissionUncertaintyRef.current?.submissionId === submissionId &&
        authScopesEqual(submissionUncertaintyRef.current?.owner ?? null, owner);
      const statusUnavailable = i18n.t("chat.runTerminal.statusUnavailable", {
        defaultValue: i18n.t("chat.requestFailed"),
      });
      try {
        const response = await sessionApi.getChatSubmission(submissionId);
        if (!isCurrentResolution()) return;
        const resolution = parseChatSubmissionResolution(response, submissionId);
        if (resolution === null) {
          submissionUncertaintyRef.current = {
            sessionId: submissionUncertaintyRef.current?.sessionId || null,
            submissionId,
            owner,
            previousMessages: submissionUncertaintyRef.current?.previousMessages,
            suppressMessageProjection: pending.suppressMessageProjection,
          };
          setPendingSubmissionId(submissionId);
          setError(statusUnavailable);
          return;
        }
        if (isAuthoritativePreLedgerAbsence(resolution)) {
          const previous = pending.suppressMessageProjection
            ? []
            : pending.previousMessages || [];
          messagesRef.current = previous;
          setMessages(previous);
          setError(null);
          advancePersistedSubmissionFence(owner, submissionId);
          return;
        }
        const outcome = resolution.outcome;
        if (resolution.state === "rejected_before_persist") {
          const previous = pending.suppressMessageProjection
            ? []
            : pending.previousMessages || [];
          messagesRef.current = previous;
          setMessages(previous);
          setError(null);
          advancePersistedSubmissionFence(owner, submissionId);
          return;
        }
        if (resolution.state === "enqueue_failed") {
          const previous = pending.suppressMessageProjection
            ? []
            : pending.previousMessages || [];
          messagesRef.current = previous;
          setMessages(previous);
          setError(i18n.t("chat.runTerminal.failed"));
          advancePersistedSubmissionFence(owner, submissionId);
          return;
        }
        if (
          resolution.state === "queued" &&
          outcome?.session_id &&
          outcome.run_id
        ) {
          submissionUncertaintyRef.current = {
            sessionId: outcome.session_id,
            submissionId,
            owner,
            previousMessages: pending.previousMessages,
          };
          setPendingSubmissionId(submissionId);
          const historyPromise = loadHistory(outcome.session_id, outcome.run_id);
          const historySessionGeneration = sessionGenerationRef.current;
          const restored = await historyPromise;
          if (
            restored !== null &&
            isCurrentResolution(historySessionGeneration)
          ) {
            setError(null);
            advancePersistedSubmissionFence(owner, submissionId);
          }
          return;
        }
        if (resolution.state === "needs_confirmation") {
          if (!outcome || !isChatStreamNeedsConfirmation(outcome)) {
            setError(statusUnavailable);
            return;
          }
          setConfirmationRecovery({
            owner,
            submissionId,
            suggestions: outcome.suggestions,
          });
          setError(null);
          advancePersistedSubmissionFence(owner, submissionId);
          return;
        }
        submissionUncertaintyRef.current = {
          sessionId: outcome?.session_id || null,
          submissionId,
          owner,
        };
        setPendingSubmissionId(submissionId);
        setError(statusUnavailable);
      } catch {
        // Missing/old-backend resolver results are unknown, never proof that
        // the original POST did not persist.
        if (isCurrentResolution()) {
          submissionUncertaintyRef.current = {
            sessionId: submissionUncertaintyRef.current?.sessionId || null,
            submissionId,
            owner,
            previousMessages: submissionUncertaintyRef.current?.previousMessages,
            suppressMessageProjection: pending.suppressMessageProjection,
          };
          setPendingSubmissionId(submissionId);
          setError(statusUnavailable);
        }
      }
    },
    [advancePersistedSubmissionFence, loadHistory],
  );

  const retryPendingSubmission = useCallback(async (): Promise<void> => {
    const pending = submissionUncertaintyRef.current;
    if (
      !pending ||
      submissionAuthIncarnationFenceRef.current !== null ||
      !authScopesEqual(authScopeRef.current, pending.owner)
    ) return;
    const authScopeGeneration = authScopeGenerationRef.current;
    const retrySessionGeneration = sessionGenerationRef.current;
    const isCurrentRetry = (expectedSessionGeneration = retrySessionGeneration) =>
      isMountedRef.current &&
      authScopeGenerationRef.current === authScopeGeneration &&
      submissionAuthIncarnationFenceRef.current === null &&
      sessionGenerationRef.current === expectedSessionGeneration &&
      authScopesEqual(authScopeRef.current, pending.owner) &&
      submissionUncertaintyRef.current?.submissionId === pending.submissionId &&
      authScopesEqual(submissionUncertaintyRef.current?.owner ?? null, pending.owner);
    const statusUnavailable = i18n.t("chat.runTerminal.statusUnavailable", {
      defaultValue: i18n.t("chat.requestFailed"),
    });
    try {
      const response = await sessionApi.retryChatSubmissionAdmission(
        pending.submissionId,
      );
      if (!isCurrentRetry()) return;
      const resolution = parseChatSubmissionResolution(response, pending.submissionId);
      if (resolution === null) {
        setError(statusUnavailable);
        return;
      }
      if (isAuthoritativePreLedgerAbsence(resolution)) {
        const previous = pending.suppressMessageProjection
          ? []
          : pending.previousMessages || [];
        messagesRef.current = previous;
        setMessages(previous);
        setError(null);
        advancePersistedSubmissionFence(pending.owner, pending.submissionId);
        return;
      }
      if (resolution.state === "rejected_before_persist") {
        const previous = pending.suppressMessageProjection
          ? []
          : pending.previousMessages || [];
        messagesRef.current = previous;
        setMessages(previous);
        setError(null);
        advancePersistedSubmissionFence(pending.owner, pending.submissionId);
        return;
      }
      if (resolution.state === "enqueue_failed") {
        const previous = pending.suppressMessageProjection
          ? []
          : pending.previousMessages || [];
        messagesRef.current = previous;
        setMessages(previous);
        setError(i18n.t("chat.runTerminal.failed"));
        advancePersistedSubmissionFence(pending.owner, pending.submissionId);
        return;
      }
      if (
        resolution.state === "queued" &&
        resolution.outcome?.session_id &&
        resolution.outcome.run_id
      ) {
        const historyPromise = loadHistory(
          resolution.outcome.session_id,
          resolution.outcome.run_id,
        );
        const historySessionGeneration = sessionGenerationRef.current;
        const restored = await historyPromise;
        if (
          restored !== null &&
          isCurrentRetry(historySessionGeneration)
        ) {
          setError(null);
          advancePersistedSubmissionFence(pending.owner, pending.submissionId);
        }
        return;
      }
      if (resolution.state === "needs_confirmation") {
        const outcome = resolution.outcome;
        if (!outcome || !isChatStreamNeedsConfirmation(outcome)) {
          setError(statusUnavailable);
          return;
        }
        setConfirmationRecovery({
          owner: pending.owner,
          submissionId: pending.submissionId,
          suggestions: outcome.suggestions,
        });
        setError(null);
        advancePersistedSubmissionFence(pending.owner, pending.submissionId);
        return;
      }
      setError(statusUnavailable);
    } catch {
      if (isCurrentRetry()) {
        setError(statusUnavailable);
      }
    }
  }, [advancePersistedSubmissionFence, loadHistory]);

  useEffect(() => {
    const pending = submissionUncertaintyRef.current;
    if (
      submissionAuthIncarnationFenceRef.current !== null ||
      authScope === null ||
      pending === null ||
      pendingSubmissionId !== pending.submissionId ||
      !authScopesEqual(pending.owner, authScope)
    ) {
      return;
    }
    const ownerKey = JSON.stringify([
      authScopeGenerationRef.current,
      pending.owner[0],
      pending.owner[1],
      pending.submissionId,
    ]);
    if (submissionResolverOwnerRef.current === ownerKey) {
      return;
    }
    submissionResolverOwnerRef.current = ownerKey;
    void resolvePersistedSubmission(pending.owner, pending.submissionId).finally(() => {
      if (submissionResolverOwnerRef.current === ownerKey) {
        submissionResolverOwnerRef.current = null;
      }
    });
  }, [
    authScope,
    browserAuthIncarnation,
    isAuthLoading,
    pendingSubmissionId,
    resolvePersistedSubmission,
  ]);

  // Reconnect function
  const handleReconnectSSE = reconcileCurrentRun;

  // Handle visibility change
  useEffect(() => {
    const handleVisibilityChange = () => {
      if (
        document.visibilityState === "visible" &&
        connectionStatus === "disconnected" &&
        sessionIdRef.current &&
        currentRunIdRef.current &&
        streamingMessageIdRef.current
      ) {
        handleReconnectSSE();
      }
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [connectionStatus, handleReconnectSSE]);

  // Handle network status changes
  useEffect(() => {
    const handleOnline = () => {
      if (
        connectionStatus === "disconnected" &&
        sessionIdRef.current &&
        currentRunIdRef.current &&
        streamingMessageIdRef.current
      ) {
        handleReconnectSSE();
      }
    };

    const handleOffline = () => {
      setConnectionStatus("disconnected");
    };

    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);

    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
    };
  }, [connectionStatus, handleReconnectSSE]);

  return {
    messages,
    isLoading,
    isLoadingHistory,
    error,
    sessionId,
    currentRunId,
    isReconnecting: connectionStatus === "reconnecting",
    connectionStatus,
    newlyCreatedSession,
    isInitializingSandbox,
    sandboxError,
    sendMessage,
    canRetryPendingSubmission: pendingSubmissionId !== null,
    retryPendingSubmission,
    stopGeneration,
    clearMessages,
    loadHistory,
    reconnectSSE: handleReconnectSSE,
    runControlLifecycle,
  };
}

// Re-export types and utilities
export type {
  UseAgentOptions,
  UseAgentReturn,
  BackendSession,
} from "./useAgent/types";
export { API_BASE } from "./useAgent/types";
