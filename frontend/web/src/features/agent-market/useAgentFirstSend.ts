import { useCallback, useEffect, useRef, useState } from "react";
import type { NavigateFunction } from "react-router-dom";
import { agentProfileApi } from "../../services/api/agentProfile";
import type { MessageAttachment } from "../../types";
import type { AgentProfilePublicProjection } from "../../types/agentProfile";
import type { SubmissionOutcome, UseAgentReturn } from "../../hooks/useAgent/types";
import { uuid } from "../../utils/uuid";
import { buildAgentMarketWorkspacePath } from "./agentMarketSelection";

const AGENT_CONVERSATION_OPERATION_STORAGE_PREFIX = "agent-conversation-operation:";
const UUID_V4_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function agentConversationOperationStorageKey(agentId: string): string {
  return `${AGENT_CONVERSATION_OPERATION_STORAGE_PREFIX}${agentId}`;
}

function browserSessionStorage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

/** Keep a caller operation stable across response-loss retries and reloads. */
export function getOrCreateAgentConversationOperationId({
  agentId,
  storage,
  createId,
}: {
  agentId: string;
  storage: Pick<Storage, "getItem" | "setItem"> | null;
  createId: () => string;
}): string | null {
  const key = agentConversationOperationStorageKey(agentId);
  if (!storage) return null;
  try {
    const existing = storage.getItem(key);
    if (existing && UUID_V4_PATTERN.test(existing)) return existing;
    const operationId = createId();
    if (!UUID_V4_PATTERN.test(operationId)) return null;
    storage.setItem(key, operationId);
    return storage.getItem(key) === operationId ? operationId : null;
  } catch {
    return null;
  }
}

export function clearAgentConversationOperationId({
  agentId,
  storage,
}: {
  agentId: string;
  storage: Pick<Storage, "removeItem"> | null;
}): void {
  if (!storage) return;
  try {
    storage.removeItem(agentConversationOperationStorageKey(agentId));
  } catch {
    // Session reset remains best-effort when browser storage is unavailable.
  }
}

interface AgentFirstSendCoordinator {
  current: Promise<string> | null;
}

interface AgentFirstSubmissionCoordinator {
  current: {
    submissionKey: string;
    promise: Promise<SubmissionOutcome>;
  } | null;
}

/** Single-flight pinned creation; the caller may submit only after bind succeeds. */
export async function ensureAgentConversationForFirstSend({
  coordinator,
  profile,
  createConversation,
  isCurrent,
  onConversationCreated,
  bindConversation,
}: {
  coordinator: AgentFirstSendCoordinator;
  profile: Pick<AgentProfilePublicProjection, "agent_id">;
  createConversation: () => ReturnType<typeof agentProfileApi.createConversation>;
  isCurrent?: () => boolean;
  onConversationCreated?: (sessionId: string) => void;
  bindConversation: (sessionId: string) => Promise<boolean>;
}): Promise<string> {
  if (!coordinator.current) {
    coordinator.current = (async () => {
      const created = await createConversation();
      const identity = created.agent_conversation;
      if (
        !created.session_id ||
        created.agent_id !== profile.agent_id ||
        !identity ||
        identity.agent_id !== profile.agent_id
      ) {
        throw new Error("agent_workspace_identity_mismatch");
      }
      if (isCurrent && !isCurrent()) {
        throw new Error("agent_workspace_creation_cancelled");
      }
      onConversationCreated?.(created.session_id);
      if (!(await bindConversation(created.session_id))) {
        throw new Error("agent_conversation_history_unavailable");
      }
      if (isCurrent && !isCurrent()) {
        throw new Error("agent_workspace_creation_cancelled");
      }
      return created.session_id;
    })();
  }
  const flight = coordinator.current;
  try {
    return await flight;
  } catch (error) {
    if (coordinator.current === flight) coordinator.current = null;
    throw error;
  }
}

/** Share the entire first-send result so click/keyboard races submit once. */
export async function submitAgentFirstMessageSingleFlight({
  coordinator,
  submissionKey,
  ensureConversation,
  isCurrent,
  agentOptions,
  submitMessage,
}: {
  coordinator: AgentFirstSubmissionCoordinator;
  submissionKey: string;
  ensureConversation: () => Promise<string>;
  isCurrent?: () => boolean;
  agentOptions?: Record<string, boolean | string | number>;
  submitMessage: (
    sessionId: string,
    agentOptions?: Record<string, boolean | string | number>,
  ) => Promise<SubmissionOutcome>;
}): Promise<SubmissionOutcome> {
  const active = coordinator.current;
  if (active && active.submissionKey !== submissionKey) {
    return { status: "failed" };
  }
  let flight = active?.promise;
  if (!active) {
    flight = (async () => {
      let createdSessionId: string;
      try {
        createdSessionId = await ensureConversation();
      } catch (error) {
        if (isCurrent && !isCurrent()) return { status: "failed" };
        throw error;
      }
      if (isCurrent && !isCurrent()) return { status: "failed" };
      return submitMessage(createdSessionId, agentOptions);
    })();
    coordinator.current = { submissionKey, promise: flight };
  }
  try {
    if (!flight) return { status: "failed" };
    return await flight;
  } finally {
    if (coordinator.current?.promise === flight) {
      coordinator.current = null;
    }
  }
}

interface UseAgentFirstSendOptions {
  agentWorkspace?: AgentProfilePublicProjection;
  startProfile?: AgentProfilePublicProjection;
  readOnly: boolean;
  loadHistory: UseAgentReturn["loadHistory"];
  sendMessage: UseAgentReturn["sendMessage"];
  navigate: NavigateFunction;
  onSessionCreated?: (sessionId: string) => void;
}

/** Own Agent creation/submission flights and draft handoff, not the shared chat runtime. */
export function useAgentFirstSend({
  agentWorkspace,
  startProfile,
  readOnly,
  loadHistory,
  sendMessage,
  navigate,
  onSessionCreated,
}: UseAgentFirstSendOptions) {
  const creationRef = useRef<Promise<string> | null>(null);
  const submissionRef = useRef<AgentFirstSubmissionCoordinator["current"]>(null);
  const generationRef = useRef(0);
  const draftHandoffIdentityRef = useRef<string | null>(null);
  const [draftHandoffKey, setDraftHandoffKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const invalidate = useCallback(() => {
    generationRef.current += 1;
    creationRef.current = null;
    submissionRef.current = null;
    draftHandoffIdentityRef.current = null;
    setDraftHandoffKey(null);
  }, []);

  useEffect(() => () => {
    generationRef.current += 1;
  }, []);

  const clearError = useCallback(() => setError(null), []);
  const resetForIdentity = useCallback((identityKey: string) => {
    if (draftHandoffIdentityRef.current !== identityKey) invalidate();
    clearError();
  }, [clearError, invalidate]);

  const resetForNewTask = useCallback(() => {
    if (!agentWorkspace) return;
    clearAgentConversationOperationId({
      agentId: agentWorkspace.agent_id,
      storage: browserSessionStorage(),
    });
    invalidate();
    clearError();
  }, [agentWorkspace, clearError, invalidate]);

  const sendFirstMessage = useCallback(async (
    content: string,
    options?: Record<string, boolean | string | number>,
    attachments?: MessageAttachment[],
  ): Promise<SubmissionOutcome> => {
    if (readOnly || !startProfile) {
      setError("该专家已撤回或当前账号无权使用，历史会话仅供查看。");
      return { status: "failed" };
    }
    if (!agentWorkspace || startProfile.agent_id !== agentWorkspace.agent_id) {
      if (agentWorkspace) navigate(buildAgentMarketWorkspacePath(startProfile), { replace: true });
      return { status: "failed" };
    }

    const generation = generationRef.current;
    const isCurrent = () => generationRef.current === generation;
    try {
      const outcome = await submitAgentFirstMessageSingleFlight({
        coordinator: submissionRef,
        submissionKey: JSON.stringify({
          content,
          fileIds: (attachments ?? []).map((attachment) => attachment.key),
        }),
        isCurrent,
        ensureConversation: () => ensureAgentConversationForFirstSend({
          coordinator: creationRef,
          profile: startProfile,
          createConversation: () => {
            const operationId = getOrCreateAgentConversationOperationId({
              agentId: startProfile.agent_id,
              storage: browserSessionStorage(),
              createId: uuid,
            });
            if (!operationId) {
              return Promise.reject(new Error("agent_conversation_operation_storage_unavailable"));
            }
            return agentProfileApi.createConversation({ agent_id: startProfile.agent_id }, operationId);
          },
          isCurrent,
          onConversationCreated: (sessionId) => {
            draftHandoffIdentityRef.current = `${startProfile.agent_id}:${sessionId}`;
            setDraftHandoffKey(sessionId);
          },
          bindConversation: async (sessionId) => Boolean(await loadHistory(sessionId)),
        }),
        agentOptions: options,
        submitMessage: async (sessionId, firstSendOptions) => {
          clearError();
          const submission = sendMessage(content, firstSendOptions, attachments, null);
          onSessionCreated?.(sessionId);
          return submission;
        },
      });
      if (!isCurrent()) return { status: "failed" };
      if (outcome.status === "accepted") {
        clearAgentConversationOperationId({
          agentId: startProfile.agent_id,
          storage: browserSessionStorage(),
        });
      }
      return outcome;
    } catch (cause) {
      if (!isCurrent()) return { status: "failed" };
      invalidate();
      const status = cause !== null && typeof cause === "object"
        ? (cause as { status?: number }).status : undefined;
      setError(cause instanceof Error && cause.message === "agent_conversation_operation_storage_unavailable"
        ? "浏览器无法安全保存本次创建标识，请启用会话存储后重试。"
        : status === 403
          ? "当前账号无权使用该专家。"
          : status === 404 || status === 409
            ? "该专家已撤回或当前版本不可用，请返回市场重新选择。"
            : "暂时无法创建专家对话，请稍后重试。");
      return { status: "failed" };
    }
  }, [agentWorkspace, clearError, invalidate, loadHistory, navigate, onSessionCreated, readOnly, sendMessage, startProfile]);

  return { draftHandoffKey, error, clearError, resetForIdentity, resetForNewTask, sendFirstMessage };
}
