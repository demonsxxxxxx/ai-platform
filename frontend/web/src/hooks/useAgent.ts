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
import { clearAllLoadingStates } from "./useAgent/messageParts";
import { type EventHandlerContext } from "./useAgent/eventHandlers";
import {
  connectToSSE,
  reconnectSSE,
  clearReconnectTimeout,
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

  // Stream version to invalidate stale SSE events after clearMessages
  const streamVersionRef = useRef(0);

  // Keep sessionId/runId in ref for closure access
  const sessionIdRef = useRef<string | null>(null);
  const currentRunIdRef = useRef<string | null>(null);
  const messagesRef = useRef<Message[]>([]);

  useEffect(() => {
    sessionIdRef.current = sessionId;
  }, [sessionId]);

  useEffect(() => {
    currentRunIdRef.current = currentRunId;
  }, [currentRunId]);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  // Create event handler context
  const createEventHandlerContext = useCallback(
    (): EventHandlerContext => ({
      options,
      sessionIdRef,
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
    }),
    [options],
  );

  // Create SSE connection context
  const createSSEContext = useCallback(
    (): SSEConnectionContext => ({
      ...createEventHandlerContext(),
      abortControllerRef,
      isConnectingRef,
      streamingMessageIdRef,
      reconnectTimeoutRef,
      retryCountRef,
      messagesRef,
    }),
    [createEventHandlerContext],
  );

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
      clearReconnectTimeout(reconnectTimeoutRef);
    };
  }, []);

  // Load message history from backend
  const loadHistory = useCallback(
    async (targetSessionId: string, targetRunId?: string) => {
      if (isLoadingHistoryRef.current) {
        console.log(
          "[loadHistory] Switching to new session, aborting previous load...",
        );
      }
      const historyLoadToken = beginHistoryLoad(historyLoadTokenRef);
      const isCurrentHistoryLoadRequest = () =>
        isCurrentHistoryLoad(historyLoadTokenRef, historyLoadToken);

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
          setSessionId(targetSessionId);
          setSessionAgentId(sessionData.agent_id || DEFAULT_CHAT_AGENT_ID);
          setCurrentProjectId(
            (sessionData.metadata?.project_id as string) || null,
          );

          const statusRunId = resolveHistoryCurrentRunId({
            targetRunId,
            sessionData,
          });

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

          // 并行发起 events、status 和 feedback 请求，减少串行等待时间
          const eventsPromise = sessionApi.getEvents(targetSessionId);
          const statusPromise = statusRunId
            ? sessionApi.getStatus(targetSessionId, statusRunId).catch((e) => {
                console.warn("[loadHistory] Failed to check status:", e);
                return null;
              })
            : Promise.resolve(null);
          const feedbackPromise = canReadFeedback
            ? feedbackApi
                .list(0, 100, undefined, undefined, targetSessionId)
                .catch((e) => {
                  console.warn("[loadHistory] Failed to load feedback:", e);
                  return null;
                })
            : Promise.resolve(null);

          const [eventsData, statusData, feedbackList] = await Promise.all([
            eventsPromise,
            statusPromise,
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
          setCurrentRunId(historyCurrentRunId);
          currentRunIdRef.current = historyCurrentRunId;

          let isTaskRunning = false;
          if (statusData) {
            isTaskRunning =
              statusData.status === "pending" ||
              statusData.status === "running";
          }

          if (eventsData.events && eventsData.events.length > 0) {
            let reconstructedMessages = reconstructMessagesFromEvents(
              eventsData.events as HistoryEvent[],
              processedEventIdsRef.current,
              { options, activeSubagentStack: activeSubagentStackRef.current },
            );

            // Apply feedback (already loaded in parallel)
            if (feedbackList && feedbackList.items.length > 0) {
              const feedbackMap = new Map(
                feedbackList.items.map((f) => [
                  f.run_id,
                  { feedback: f.rating, feedbackId: f.id },
                ]),
              );
              reconstructedMessages = reconstructedMessages.map((msg) => {
                if (msg.runId) {
                  const feedbackInfo = feedbackMap.get(msg.runId);
                  if (feedbackInfo) {
                    return {
                      ...msg,
                      feedback: feedbackInfo.feedback,
                      feedbackId: feedbackInfo.feedbackId,
                    };
                  }
                }
                return msg;
              });
            }

            const lastTimestamp = getLastEventTimestamp(
              eventsData.events as HistoryEvent[],
            );
            if (lastTimestamp) {
              lastHistoryTimestampRef.current = lastTimestamp;
            }

            // When the task is still running, target the assistant message for
            // that same run. If history has the user message but no assistant
            // events yet, append a fresh assistant bubble after the latest user.
            if (isTaskRunning && historyCurrentRunId) {
              const prepared = prepareMessagesForRunningRun(
                reconstructedMessages,
                historyCurrentRunId,
              );
              reconstructedMessages = prepared.messages;
              const streamingMessageId = prepared.streamingMessageId;

              setMessages(reconstructedMessages);

              // Fire-and-forget SSE reconnect so that loadHistory
              // returns sessionConfig immediately, allowing the caller
              // (useSessionSync) to restore model selection and other UI
              // state without being blocked by the long-lived connection.
              isReconnectFromHistoryRef.current = false;
              const ctx = createSSEContext();
              connectToSSE(
                targetSessionId,
                historyCurrentRunId,
                streamingMessageId,
                ctx,
              ).catch((e) => {
                console.warn("[loadHistory] SSE reconnect failed:", e);
              });
            } else {
              setMessages(reconstructedMessages);
            }
          } else {
            setMessages([]);

            if (isTaskRunning && historyCurrentRunId) {
              isReconnectFromHistoryRef.current = false;

              const streamingMessageId = uuid();
              const prepared = prepareMessagesForRunningRun(
                [],
                historyCurrentRunId,
                () => streamingMessageId,
              );
              setMessages(prepared.messages);
              // Fire-and-forget SSE reconnect (same reason as above).
              const ctx = createSSEContext();
              connectToSSE(
                targetSessionId,
                historyCurrentRunId,
                streamingMessageId,
                ctx,
              ).catch((e) => {
                console.warn("[loadHistory] SSE reconnect failed:", e);
              });
            }
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
    [options, createSSEContext, canReadFeedback],
  );

  // Send message
  const sendMessage = useCallback(
    async (
      content: string,
      agentOptions?: Record<string, boolean | string | number>,
      attachments?: MessageAttachment[],
      selectedSkill?: SelectedSkillRequest | null,
    ): Promise<SubmissionOutcome> => {
      if (!content.trim()) return { status: "failed" };

      if (isSendingRef.current) {
        console.log(
          "[sendMessage] Already sending, ignoring duplicate request",
        );
        return { status: "failed" };
      }
      isSendingRef.current = true;

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
        if (sessionId) {
          sessionApi.markRead(sessionId).catch(() => {});
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
          sessionId ?? undefined,
          fullAgentOptions,
          attachments,
          pendingProjectIdRef.current ?? undefined,
          disabledSkills,
          disabledMcpTools,
          selectedSkill,
          sessionAgentId,
        );

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
          isSendingRef.current = false;
          return { status: "accepted" };
        }

        const newSessionId = submitData.session_id;
        const newRunId = submitData.run_id;
        const routedAgentId = resolveChatSessionAgentId(
          submitData,
          sessionAgentId,
        );
        const projectId = pendingProjectIdRef.current;
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

        if (!sessionId && newSessionId) {
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
              setNewlyCreatedSession((prev) =>
                prev
                  ? {
                      ...prev,
                      name: result.title,
                      updated_at: new Date().toISOString(),
                    }
                  : null,
              );
              dispatchSessionTitleUpdated({
                sessionId: newSessionId,
                title: result.title,
              });
            })
            .catch((err) => {
              console.warn("[sendMessage] Failed to generate title:", err);
            });
        } else if (sessionId && newRunId) {
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

        const streamSessionId = newSessionId || sessionId;
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
          .catch(() => {
            toast.dismiss("chat-queue");
            const streamError = i18n.t("chat.requestFailed");
            setError(streamError);
            setMessages((prev) =>
              prev.map((message) =>
                message.id === finalAssistantMessageId
                  ? {
                      ...message,
                      content: i18n.t("chat.errorPrefix", {
                        error: streamError,
                      }),
                      isStreaming: false,
                      parts: clearAllLoadingStates(message.parts || []),
                    }
                  : message,
              ),
            );
            setConnectionStatus("disconnected");
            setIsInitializingSandbox(false);
          })
          .finally(() => {
            setIsLoading(false);
            isSendingRef.current = false;
          });
        return { status: "accepted" };
      } catch (err) {
        toast.dismiss("chat-queue");
        if (err instanceof Error && err.name === "AbortError") {
          setIsLoading(false);
          isSendingRef.current = false;
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
          isSendingRef.current = false;
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
        isSendingRef.current = false;
        return { status: "failed" };
      }
    },
    [
      sessionId,
      sessionAgentId,
      createSSEContext,
      newlyCreatedSession?.metadata,
      options,
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
    streamVersionRef.current += 1;
    setMessages([]);
    setSessionId(null);
    setSessionAgentId(DEFAULT_CHAT_AGENT_ID);
    setError(null);
    setCurrentRunId(null);
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
  const handleReconnectSSE = useCallback(async () => {
    const ctx = {
      ...createSSEContext(),
      sessionIdRef,
      currentRunIdRef,
      isReconnectFromHistoryRef,
    };
    await reconnectSSE(ctx);
  }, [createSSEContext]);

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
