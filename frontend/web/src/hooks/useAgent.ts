/**
 * Main useAgent hook
 * Provides agent communication, message management, and SSE streaming
 */

import { useState, useCallback, useRef, useEffect } from "react";
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
import { feedbackApi } from "../services/api/feedback";
import { useAuth } from "../hooks/useAuth";
import { Permission } from "../types/auth";
import {
  type UseAgentOptions,
  type SubagentStackItem,
  type HistoryEvent,
  type SubmissionOutcome,
  type UseAgentReturn,
} from "./useAgent/types";
import {
  reconstructMessagesFromEvents,
  getLastEventTimestamp,
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
import { type EventHandlerContext } from "./useAgent/eventHandlers";
import {
  connectToSSE,
  reconnectSSE,
  clearReconnectTimeout,
  queryAuthoritativeRunStatus,
  type SSEConnectionContext,
} from "./useAgent/sseConnection";
import { createOptimisticMessagesForSend } from "./useAgent/optimisticMessages";
import { translateBackendError } from "../utils/backendErrors";
import { dispatchSessionTitleUpdated } from "../utils/sessionTitleEvents";
import {
  SELECTED_SKILL_RECOVERABLE_CODES,
  type SelectedSkillRecoverableCode,
} from "./useSelectedSkillTask";

function getSelectedSkillRecoverableCode(
  error: unknown,
): SelectedSkillRecoverableCode | null {
  if (!(error instanceof Error)) return null;
  return (
    SELECTED_SKILL_RECOVERABLE_CODES.find(
      (code) => error.message.trim() === code,
    ) ?? null
  );
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

export function useAgent(options?: UseAgentOptions): UseAgentReturn {
  const { hasAnyPermission } = useAuth();
  const canReadFeedback = hasAnyPermission([
    Permission.FEEDBACK_READ,
    Permission.FEEDBACK_WRITE,
  ]);

  // State
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessionAgentId, setSessionAgentId] = useState(DEFAULT_CHAT_AGENT_ID);
  const [currentProjectId, setCurrentProjectId] = useState<string | null>(null);
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
  const pendingProjectIdRef = useRef<string | null>(null);
  const autoExpandProjectIdRef = useRef<string | null>(null);
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

  // Track processed event IDs to prevent duplicates
  const processedEventIdsRef = useRef<Set<string>>(new Set());

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

  // Stream version to invalidate stale SSE events after clearMessages
  const streamVersionRef = useRef(0);

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

  const convergeRunLifecycle = useCallback(
    (
      runId: string,
      outcome: TerminalRunStatus | "status_unavailable",
      messageId: string,
    ): boolean => {
      if (!isMountedRef.current || currentRunIdRef.current !== runId) {
        return false;
      }

      currentRunIdRef.current = null;
      streamVersionRef.current += 1;
      clearReconnectTimeout(reconnectTimeoutRef);
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
        abortControllerRef.current = null;
      }
      isConnectingRef.current = false;
      streamingMessageIdRef.current = null;
      isSendingRef.current = false;
      statusRetryCountRef.current = 0;

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
    [options],
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

  // Create event handler context
  const createEventHandlerContext = useCallback(
    (): EventHandlerContext => ({
      options,
      sessionIdRef,
      currentRunIdRef,
      processedEventIdsRef,
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
    }),
    [createEventHandlerContext],
  );

  const reconcileCurrentRun = useCallback(async () => {
    if (!isMountedRef.current) {
      return;
    }
    const ctx = {
      ...createSSEContext(),
      sessionIdRef,
      currentRunIdRef,
      isReconnectFromHistoryRef,
    };
    await reconnectSSE(ctx);
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
      sessionGenerationRef.current += 1;
      submissionTokenRef.current += 1;
      streamVersionRef.current += 1;
      statusRetryCountRef.current = 0;
      pendingProjectIdRef.current = null;
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
      clearReconnectTimeout(reconnectTimeoutRef);
    };
  }, []);

  // Load message history from backend
  const loadHistory = useCallback(
    async (targetSessionId: string, targetRunId?: string) => {
      if (!isMountedRef.current) {
        return null;
      }
      const mountedGeneration = mountedGenerationRef.current;
      if (isLoadingHistoryRef.current) {
        console.log(
          "[loadHistory] Switching to new session, aborting previous load...",
        );
      }
      const historyLoadToken = beginHistoryLoad(historyLoadTokenRef);
      const isCurrentHistoryLoadRequest = () =>
        isMountedRef.current &&
        mountedGenerationRef.current === mountedGeneration &&
        isCurrentHistoryLoad(historyLoadTokenRef, historyLoadToken);
      sessionGenerationRef.current += 1;
      submissionTokenRef.current += 1;
      streamVersionRef.current += 1;
      isSendingRef.current = false;
      retryCountRef.current = 0;
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
          setCurrentProjectId(
            (sessionData.metadata?.project_id as string) || null,
          );

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
          const eventsPromise = sessionApi.getEvents(targetSessionId);
          const feedbackPromise = canReadFeedback
            ? feedbackApi
                .list(0, 100, undefined, undefined, targetSessionId)
                .catch((e) => {
                  console.warn("[loadHistory] Failed to load feedback:", e);
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
          setMessages(reconstructedMessages);

          const historyMessageId = historyCurrentRunId
            ? ([...reconstructedMessages]
                .reverse()
                .find(
                  (message) =>
                    message.runId === historyCurrentRunId &&
                    message.role === "assistant",
                )?.id || historyCurrentRunId)
            : null;

          if (statusUnavailable && historyCurrentRunId && historyMessageId) {
            currentRunIdRef.current = historyCurrentRunId;
            setCurrentRunId(historyCurrentRunId);
            finalizeRunStatusUnavailable(historyCurrentRunId, historyMessageId);
          } else if (terminalStatus && historyCurrentRunId && historyMessageId) {
            // The shared lifecycle converger owns terminal presentation for
            // both live SSE and restored history.
            currentRunIdRef.current = historyCurrentRunId;
            setCurrentRunId(historyCurrentRunId);
            finalizeTerminalRun(
              historyCurrentRunId,
              terminalStatus,
              historyMessageId,
            );
          } else {
            setCurrentRunId(activeHistoryRunId);
            currentRunIdRef.current = activeHistoryRunId;
          }

          if (isTaskRunning && historyCurrentRunId && streamingMessageId) {
            isReconnectFromHistoryRef.current = false;
            const ctx = createSSEContext();
            connectToSSE(
              targetSessionId,
              historyCurrentRunId,
              streamingMessageId,
              ctx,
            ).catch(() => {
              if (
                !isCurrentHistoryLoadRequest() ||
                currentRunIdRef.current !== historyCurrentRunId
              ) {
                return;
              }
              reconcileCurrentRun().catch((reconcileError) => {
                console.warn(
                  "[loadHistory] SSE reconciliation failed:",
                  reconcileError,
                );
              });
            });
          }

          // Return sessionConfig *before* any SSE reconnect so that the
          // caller can immediately restore model selection / agent / config.

          return sessionConfig;
        }
      } catch (err) {
        if (isCurrentHistoryLoadRequest()) {
          console.error("Failed to load session:", err);
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
      finalizeTerminalRun,
      finalizeRunStatusUnavailable,
      reconcileCurrentRun,
    ],
  );

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

      if (isSendingRef.current) {
        console.log(
          "[sendMessage] Already sending, ignoring duplicate request",
        );
        return { status: "failed" };
      }
      isSendingRef.current = true;
      const submissionToken = ++submissionTokenRef.current;
      const mountedGeneration = mountedGenerationRef.current;
      const requestSessionGeneration = sessionGenerationRef.current;
      const requestSessionId = sessionIdRef.current;
      const requestAgentId = sessionAgentIdRef.current;
      streamVersionRef.current += 1;
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
      lastHistoryTimestampRef.current = null;

      const previousMessages = messagesRef.current;
      const { messages: optimisticMessages, assistantMessageId } =
        createOptimisticMessagesForSend({
          previousMessages,
          content,
          attachments,
        });

      setMessages(optimisticMessages);
      setIsLoading(true);
      setError(null);
      let finalAssistantMessageId = assistantMessageId;

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

        const submitData: ChatStreamResponse = await sessionApi.submitChat(
          content,
          requestSessionId ?? undefined,
          fullAgentOptions,
          attachments,
          pendingProjectIdRef.current ?? undefined,
          disabledSkills,
          disabledMcpTools,
          selectedSkill,
          requestAgentId,
        );

        if (!isCurrentRequestSession()) {
          return { status: "failed" };
        }

        if (isChatStreamNeedsConfirmation(submitData)) {
          pendingProjectIdRef.current = null;
          const confirmationMessage = formatConfirmationMessage(
            submitData.suggestions,
          );
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMessageId
                ? {
                    ...m,
                    content: confirmationMessage,
                    isStreaming: false,
                    parts: [{ type: "text", content: confirmationMessage }],
                  }
                : m,
            ),
          );
          setConnectionStatus("disconnected");
          setIsInitializingSandbox(false);
          setIsLoading(false);
          finishCurrentSubmission();
          return { status: "accepted" };
        }

        const newSessionId = submitData.session_id;
        const newRunId = submitData.run_id;
        const routedAgentId = resolveChatSessionAgentId(
          submitData,
          requestAgentId,
        );
        const projectId = pendingProjectIdRef.current;
        sessionAgentIdRef.current = routedAgentId;
        setSessionAgentId(routedAgentId);

        // Clear pending project ID after use
        pendingProjectIdRef.current = null;

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
          if (projectId) {
            conversationConfig.project_id = projectId;
          }

          const newSession: BackendSession = {
            id: newSessionId,
            agent_id: routedAgentId,
            created_at: now,
            updated_at: now,
            is_active: true,
            metadata: conversationConfig,
          };
          setNewlyCreatedSession(newSession);
          setCurrentProjectId(projectId);

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
            .catch((err) => {
              console.warn("[sendMessage] Failed to generate title:", err);
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
          setCurrentRunId(newRunId);
          currentRunIdRef.current = newRunId;
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMessageId
                ? {
                    ...m,
                    id: newRunId,
                    runId: newRunId,
                  }
                : m,
            ),
          );
        }

        const streamSessionId = newSessionId || requestSessionId;
        const streamRunId = newRunId;
        finalAssistantMessageId = newRunId || assistantMessageId;

        if (!streamSessionId || !streamRunId) {
          throw new Error("Missing session_id or run_id");
        }

        isReconnectFromHistoryRef.current = false;
        const ctx = createSSEContext();
        void connectToSSE(
          streamSessionId,
          streamRunId,
          finalAssistantMessageId,
          ctx,
        )
          .catch(async () => {
            if (
              !isCurrentSubmission() ||
              currentRunIdRef.current !== streamRunId
            ) {
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
          return { status: "failed" };
        }
        toast.dismiss("chat-queue");
        if (err instanceof Error && err.name === "AbortError") {
          setIsLoading(false);
          finishCurrentSubmission();
          return { status: "failed" };
        }
        const recoverableCode = selectedSkill
          ? getSelectedSkillRecoverableCode(err)
          : null;
        if (recoverableCode) {
          setMessages(previousMessages);
          setConnectionStatus("disconnected");
          setIsInitializingSandbox(false);
          setIsLoading(false);
          finishCurrentSubmission();
          return { status: "recoverable_error", code: recoverableCode };
        }
        const errorMessage =
          err instanceof Error
            ? translateBackendError(err.message, i18n.t.bind(i18n))
            : i18n.t("chat.unknownError");
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
      reconcileCurrentRun,
    ],
  );

  const stopGeneration = useCallback(async () => {
    const currentRunId = currentRunIdRef.current;
    if (!currentRunId) {
      return;
    }

    isSendingRef.current = false;
    setIsLoading(false);
    toast.dismiss("chat-queue");
    setIsInitializingSandbox(false);
    setSandboxError(null);

    // Clear approvals immediately (don't wait for SSE cancel event which may never arrive)
    options?.onClearApprovals?.();

    // Clear loading states on all messages and their parts
    setMessages((prev) =>
      prev.map((m) => ({
        ...m,
        isStreaming: false,
        parts: clearAllLoadingStates(m.parts || []),
      })),
    );

    try {
      await sessionApi.cancelRun(currentRunId);
    } catch (error) {
      console.error(
        "[stopGeneration] Failed to call backend cancel API:",
        error,
      );
    }
  }, [options]);

  const clearMessages = useCallback(() => {
    // Invalidate every asynchronous owner before clearing React state so a
    // delayed submit or history restore cannot repopulate this blank session.
    historyLoadTokenRef.current += 1;
    sessionGenerationRef.current += 1;
    submissionTokenRef.current += 1;
    streamVersionRef.current += 1;
    pendingProjectIdRef.current = null;
    isLoadingHistoryRef.current = false;
    isSendingRef.current = false;
    isConnectingRef.current = false;
    retryCountRef.current = 0;
    statusRetryCountRef.current = 0;
    setMessages([]);
    setSessionId(null);
    sessionAgentIdRef.current = DEFAULT_CHAT_AGENT_ID;
    setSessionAgentId(DEFAULT_CHAT_AGENT_ID);
    setError(null);
    setCurrentRunId(null);
    setCurrentProjectId(null);
    setNewlyCreatedSession(null);
    setIsLoading(false);
    setIsLoadingHistory(false);
    setIsInitializingSandbox(false);
    setSandboxError(null);
    setConnectionStatus("disconnected");
    processedEventIdsRef.current.clear();
    lastHistoryTimestampRef.current = null;
    streamingMessageIdRef.current = null;
    sessionIdRef.current = null;
    currentRunIdRef.current = null;
    activeSubagentStackRef.current = [];
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    clearReconnectTimeout(reconnectTimeoutRef);
  }, []);

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
    stopGeneration,
    clearMessages,
    loadHistory,
    reconnectSSE: handleReconnectSSE,
    setPendingProjectId: (id: string | null) => {
      pendingProjectIdRef.current = id;
      autoExpandProjectIdRef.current = id;
    },
    autoExpandProjectId: autoExpandProjectIdRef.current,
    clearAutoExpandProjectId: (id?: string | null) => {
      if (
        id === undefined ||
        id === null ||
        autoExpandProjectIdRef.current === id
      ) {
        autoExpandProjectIdRef.current = null;
      }
    },
    currentProjectId,
  };
}

// Re-export types and utilities
export type {
  UseAgentOptions,
  UseAgentReturn,
  BackendSession,
} from "./useAgent/types";
export { API_BASE } from "./useAgent/types";
