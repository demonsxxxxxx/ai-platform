import type { ExternalStoreThreadListAdapter } from "@assistant-ui/react";
import { Button } from "@lobehub/ui/base-ui";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { History } from "lucide-react";
import { BlockPreviewPortal } from "../../chat/ChatMessage/items/McpBlockPreview";
import { SessionSidebar } from "../../panels/SessionSidebar";
import type { SessionSidebarHandle } from "../../panels/SessionSidebar";
import type { SessionSidebarSessionSource } from "../../panels/SessionSidebar";
import { useModelCatalogContext } from "../../../contexts/ModelCatalogContext";
import { useAgent } from "../../../hooks/useAgent";
import { useAuth } from "../../../hooks/useAuth";
import {
  canSelectChatMcpTools,
  ChatMcpCatalogContext,
  hasValidatedChatMcpCatalog,
  reconcileChatMcpToolSelection,
  useTools,
} from "../../../hooks/useTools";
import { useSkills } from "../../../hooks/useSkills";
import { useSelectedSkillTask, type SelectedSkillTaskState } from "../../../hooks/useSelectedSkillTask";
import { useSessionConfig } from "../../../hooks/useSessionConfig";
import {
  Permission,
  type MessageAttachment,
  type SelectedSkillRequest,
  type ToolCategory,
} from "../../../types";
import type { SubmissionOutcome } from "../../../hooks/useAgent/types";
import { useDragAndDrop } from "./useDragAndDrop";
import { useWebSocketNotifications } from "./useWebSocketNotifications";
import { useAgentOptions } from "./useAgentOptions";
import {
  useConversationRouteIdentityReset,
  useSessionSync,
} from "./useSessionSync";
import {
  getExternalNavigationTargetFile,
  shouldScrollToBottomAfterExternalNavigation,
} from "./externalNavigationState";
import { reconcileCurrentModelSelection } from "./modelSelection";
import { getRestoredModelSelection } from "./sessionState";
import {
  buildEffectiveSkills,
  countEnabledSkills,
  resolveComposerSkillsAvailability,
} from "./skillAvailability";
import { AppShell } from "./AppShell";
import { ChatView } from "./ChatView";
import { WorkbenchShell } from "../../workbench/WorkbenchShell";
import { CHAT_AGENT_OPTION_DEFINITIONS } from "../../../types/agentOptions";
import { shouldShowMessageOutline } from "./messageOutline";
import { RunPlaybackPanel } from "./RunPlaybackPanel";
import { FailureGuidanceCard } from "../../common/FailureGuidanceCard";
import { openPersistentToolPanel } from "../../chat/ChatMessage/items/persistentToolPanelState";
import type { BackendSession } from "../../../services/api";
import type { AgentProfilePublicProjection } from "../../../types/agentProfile";
import { useAgentFirstSend } from "../../../features/agent-market/useAgentFirstSend";
import { useAgentChatPresentation } from "../../../features/agent-market/useAgentChatPresentation";
import {
  exposeGenericChatControl,
  getChatToolAccess,
  isExactAgentWorkspaceBinding,
  projectAgentWorkspaceTranscript,
  useAgentConversationBinding,
} from "../../../features/agent-market/useAgentConversationBinding";
import {
  buildAgentMarketWorkspacePath,
  buildSessionHistoryPath,
} from "../../../features/agent-market/agentMarketSelection";

const LOCKED_SELECTED_SKILL_STATE: SelectedSkillTaskState = {
  selectedSkill: null, status: "idle", recoveryCode: null, requiresReconfirmation: false,
};

export interface ChatAppContentProps {
  sidebarCollapsed: boolean;
  setSidebarCollapsed: (collapsed: boolean) => void;
  mobileSidebarOpen: boolean;
  setMobileSidebarOpen: (open: boolean) => void;
  agentWorkspace?: AgentProfilePublicProjection;
  agentWorkspaceStartProfile?: AgentProfilePublicProjection;
  agentWorkspaceReadOnly?: boolean;
  agentWorkspaceSessionSource?: SessionSidebarSessionSource;
  globalHistorySource?: SessionSidebarSessionSource;
  agentWorkspaceHistoryError?: string | null;
  onAgentWorkspaceHistoryRetry?: () => void;
  onAgentWorkspaceSessionCreated?: (sessionId: string) => void;
}

export function ChatAppContent({
  sidebarCollapsed,
  setSidebarCollapsed,
  mobileSidebarOpen,
  setMobileSidebarOpen,
  agentWorkspace,
  agentWorkspaceStartProfile,
  agentWorkspaceReadOnly = false,
  agentWorkspaceSessionSource,
  globalHistorySource,
  agentWorkspaceHistoryError = null,
  onAgentWorkspaceHistoryRetry,
  onAgentWorkspaceSessionCreated,
}: ChatAppContentProps) {
  const { t } = useTranslation();
  const agentChatPresentation = useAgentChatPresentation(agentWorkspace);
  const location = useLocation();
  const navigate = useNavigate();
  const { sessionId: routeSessionId } = useParams<{ sessionId?: string }>();
  const {
    state: agentConversationState,
    resetForIdentity: resetConversationBinding,
    resetForNewTask: resetConversationForNewTask,
    beginSelection: beginConversationSelection,
    recover: recoverConversationBinding,
  } = useAgentConversationBinding({ agentWorkspace, routeSessionId, navigate });
  const agentWorkspaceRouteBasePath = agentWorkspace
    ? buildAgentMarketWorkspacePath(agentWorkspace)
    : "/chat";
  const agentWorkspaceStarterDraft = useMemo(() => {
    if (!agentWorkspace) return "";
    const routeState = location.state as { agentStarterPrompt?: unknown } | null;
    const prompt = routeState?.agentStarterPrompt;
    return typeof prompt === "string" && agentWorkspace.starter_prompts.includes(prompt)
      ? prompt
      : "";
  }, [agentWorkspace, location.state]);
  const chatToolAccess = getChatToolAccess({
    agentWorkspace,
    phase: agentConversationState.phase,
    sessionId: null,
  });
  const agentConversationControlsLocked = !chatToolAccess.enabled;
  const {
    availableModels,
    defaultModel,
    defaultModelId,
    refreshModels,
    selectedGroupId,
    selectedGroup,
    selectionError: catalogSelectionError,
    isLoading: modelsLoading,
  } = useModelCatalogContext();
  const { user, hasPermission, isAuthenticated } = useAuth();
  const composerSkillsProbeAvailability = resolveComposerSkillsAvailability({
    isAuthenticated,
    catalogEffectivePermissions: [],
    catalogPermissionsKnown: false,
  });

  const {
    isPageDragging,
    pageDragAttachments,
    setPageDragAttachments,
    clearPageDragAttachments,
    uploadControls,
  } = useDragAndDrop();

  const {
    skills,
    isLoading: skillsLoading,
    listError: skillsListError,
    fetchSkills,
    effectivePermissions: skillsEffectivePermissions,
    effectivePermissionsKnown: skillsEffectivePermissionsKnown,
  } = useSkills({
    enabled:
      !agentConversationControlsLocked &&
      composerSkillsProbeAvailability.shouldFetchSkills,
    allAuthorizedCatalog: true,
    identityScope: JSON.stringify([user?.tenant_id, user?.id, user?.roles, user?.permissions, user?.is_admin, user?.department_ids, user?.direct_department_ids, user?.department_authority_status]),
  });
  const {
    state: selectedSkillState,
    selectSkill,
    clearSelection: clearSelectedSkill,
    recover: recoverSelectedSkill,
    markFilesReady: markSelectedSkillFilesReady,
  } = useSelectedSkillTask({
    skills,
    skillsLoading,
    skillsError: skillsListError,
    refreshSkills: fetchSkills,
  });
  const composerSkillsAvailability = resolveComposerSkillsAvailability({
    isAuthenticated,
    catalogEffectivePermissions: skillsEffectivePermissions,
    catalogPermissionsKnown: skillsEffectivePermissionsKnown,
  });

  const sessionConfigRef = useRef({
    disabledSkills: [] as string[],
    selectedMcpToolIds: undefined as string[] | undefined,
    agentOptions: {} as Record<string, boolean | string | number>,
  });

  const {
    messages,
    sessionId,
    currentRunId,
    runInputs,
    continuation,
    continuationPending,
    canStopGeneration,
    isLoading,
    isStopping,
    stopUnconfirmed,
    isLoadingHistory,
    connectionStatus,
    failureGuidance,
    historyReadError,
    retryHistoryLoad,
    newlyCreatedSession,
    sendMessage,
    canRetryPendingSubmission,
    retryPendingSubmission,
    stopGeneration,
    clearMessages,
    loadHistory,
    reconnectSSE,
    runControlLifecycle,
  } = useAgent({
    getDisabledSkills: () => sessionConfigRef.current.disabledSkills,
    // The legacy callback type says ``string[]``. The runtime deliberately
    // preserves ``undefined`` so an omitted selection can inherit from the
    // authoritative server session.
    getDisabledMcpTools: () =>
      sessionConfigRef.current.selectedMcpToolIds as string[],
    getAgentOptions: () => sessionConfigRef.current.agentOptions,
  });

  const {
    draftHandoffKey: agentWorkspaceDraftHandoffKey,
    error: agentWorkspaceError,
    clearError: clearAgentWorkspaceError,
    resetForIdentity: resetAgentFirstSendIdentity,
    resetForNewTask: resetAgentFirstSendTask,
    sendFirstMessage: sendAgentFirstMessage,
  } = useAgentFirstSend({
    agentWorkspace,
    startProfile: agentWorkspaceStartProfile,
    readOnly: agentWorkspaceReadOnly,
    loadHistory,
    sendMessage,
    navigate,
    onSessionCreated: onAgentWorkspaceSessionCreated,
  });

  const sessionToolAccess = getChatToolAccess({
    agentWorkspace,
    phase: agentConversationState.phase,
    sessionId,
  });
  const agentConversationTargetSessionId = agentWorkspace
    ? routeSessionId ?? null
    : routeSessionId ?? sessionId;
  const conversationIdentityKey = agentWorkspace
    ? `${agentWorkspace.agent_id}:${routeSessionId ?? ""}`
    : `generic:${routeSessionId ?? ""}`;

  // Clear before paint whenever the rendered workspace/session identity changes.
  useConversationRouteIdentityReset({
    conversationIdentityKey,
    hasAgentWorkspace: Boolean(agentWorkspace),
    routeSessionId,
    sessionId,
    onIdentityChange: () => {
      resetAgentFirstSendIdentity(conversationIdentityKey);
      clearMessages();
      clearPageDragAttachments();
      // A task Skill is scoped to the composer that selected it. A route or
      // workspace identity change clears the session, so it must also clear the
      // local selector before a later submit can create an unbound conversation.
      clearSelectedSkill();
      resetConversationBinding();
    },
  });

  useEffect(() => recoverConversationBinding(agentConversationTargetSessionId), [
    agentConversationTargetSessionId,
    recoverConversationBinding,
  ]);

  const {
    tools,
    serverSelectedToolIds,
    totalCount: totalToolsCount,
    catalogState: mcpCatalogState,
    refreshTools,
  } = useTools({
    enabled: sessionToolAccess.enabled,
    sessionId: sessionToolAccess.sessionId,
  });

  const filteredModels = availableModels ?? null;

  const {
    agentOptionValues,
    currentAgentOptions,
    handleToggleAgentOption,
    restoreAgentOptions,
    resetAgentOptionDefaults,
  } = useAgentOptions(CHAT_AGENT_OPTION_DEFINITIONS);

  const {
    config: sessionConfig,
    toggleMcpTool: toggleSessionMcpTool,
    setSelectedMcpToolIds,
    setAgentOption: setSessionAgentOption,
    resetToDefaults,
    restoreConfig: restoreSessionConfig,
  } = useSessionConfig({
    getDefaultAgentOptions: () => agentOptionValues,
  });

  useEffect(() => {
    if (!agentConversationControlsLocked) return;
    clearSelectedSkill();
    setSelectedMcpToolIds(undefined);
  }, [
    agentConversationControlsLocked,
    clearSelectedSkill,
    setSelectedMcpToolIds,
  ]);

  const restoredMcpSelectionRef = useRef<string | null>(null);
  useEffect(() => {
    if (
      agentConversationControlsLocked ||
      !hasValidatedChatMcpCatalog(mcpCatalogState.status)
    )
      return;

    const serverSelection =
      sessionId && serverSelectedToolIds !== undefined
        ? serverSelectedToolIds
        : undefined;
    const restoreKey =
      serverSelection === undefined ? null : `${sessionId}:${JSON.stringify(serverSelection)}`;
    const shouldRestoreServerSelection =
      restoreKey !== null && restoredMcpSelectionRef.current !== restoreKey;
    if (shouldRestoreServerSelection) {
      restoredMcpSelectionRef.current = restoreKey;
    }

    const reconciled = shouldRestoreServerSelection ? serverSelection : undefined;
    if (
      reconciled === undefined ||
      (sessionConfig.selectedMcpToolIds !== undefined &&
        reconciled.length === sessionConfig.selectedMcpToolIds.length &&
        reconciled.every((toolId, index) => toolId === sessionConfig.selectedMcpToolIds?.[index]))
    ) {
      return;
    }
    setSelectedMcpToolIds(reconciled);
  }, [
    agentConversationControlsLocked,
    mcpCatalogState.status,
    serverSelectedToolIds,
    sessionConfig.selectedMcpToolIds,
    sessionId,
    setSelectedMcpToolIds,
    tools,
  ]);

  const authoritativeMcpSelection = useMemo(
    () =>
      reconcileChatMcpToolSelection(
        sessionConfig.selectedMcpToolIds,
        tools,
        mcpCatalogState.status,
      ),
    [mcpCatalogState.status, sessionConfig.selectedMcpToolIds, tools],
  );

  const mcpCatalogContextValue = useMemo(
    () => ({
      catalogState: mcpCatalogState,
      retryTools: agentConversationControlsLocked
        ? undefined
        : () => {
            void refreshTools();
          },
    }),
    [agentConversationControlsLocked, mcpCatalogState, refreshTools],
  );

  const canSelectMcpTools = canSelectChatMcpTools(mcpCatalogState.status);

  const [currentModelId, setCurrentModelId] = useState("");
  const [currentModelValue, setCurrentModelValue] = useState("");
  const [modelSelectionError, setModelSelectionError] = useState<string | null>(null);

  const modelGroupRef = useRef(selectedGroupId || null);

  useEffect(() => {
    if (catalogSelectionError) {
      setCurrentModelId("");
      setCurrentModelValue("");
      setModelSelectionError(catalogSelectionError);
    }
  }, [catalogSelectionError]);

  useEffect(() => {
    const priorGroupId = modelGroupRef.current;
    if (selectedGroupId) modelGroupRef.current = selectedGroupId;
    if (priorGroupId && selectedGroupId && priorGroupId !== selectedGroupId) {
      const groupDefault = availableModels?.find((model) => model.id === defaultModelId);
      setCurrentModelId(groupDefault?.id ?? "");
      setCurrentModelValue(groupDefault?.value ?? "");
      setModelSelectionError(catalogSelectionError);
      return;
    }
    if (!availableModels || catalogSelectionError) return;
    const hasPriorSelection = Boolean(currentModelId || currentModelValue);
    const stillAvailable = availableModels.filter((model) =>
      (currentModelId && model.id === currentModelId)
      || (!currentModelId && currentModelValue && model.value === currentModelValue)).length === 1;
    if (hasPriorSelection && !stillAvailable) {
      setCurrentModelId("");
      setCurrentModelValue("");
      setModelSelectionError("之前选择的模型已不可用，请重新选择模型。");
      return;
    }
    if (modelSelectionError) {
      if (modelSelectionError === "默认模型暂不可用，请手动选择同组模型。"
        && !hasPriorSelection && defaultModel) setModelSelectionError(null);
      return;
    }
    if (!currentModelId && !currentModelValue && !defaultModel) {
      setModelSelectionError("默认模型暂不可用，请手动选择同组模型。");
      return;
    }
    const nextSelection = reconcileCurrentModelSelection({
      availableModels,
      currentModelId,
      currentModelValue,
      storedDefaultId: defaultModelId,
      storedDefaultValue: defaultModel,
    });

    if (nextSelection.modelId !== currentModelId) {
      setCurrentModelId(nextSelection.modelId);
    }
    if (nextSelection.modelValue !== currentModelValue) {
      setCurrentModelValue(nextSelection.modelValue);
    }
  }, [availableModels, catalogSelectionError, currentModelId, currentModelValue, defaultModel, defaultModelId, modelSelectionError, selectedGroupId]);

  useEffect(() => {
    handleToggleAgentOption("model", currentModelValue);
    setSessionAgentOption("model", currentModelValue);
    handleToggleAgentOption("model_id", currentModelId);
    setSessionAgentOption("model_id", currentModelId);
  }, [
    currentModelValue,
    currentModelId,
    handleToggleAgentOption,
    setSessionAgentOption,
  ]);

  const handleSelectModel = useCallback(
    (modelId: string, modelValue: string) => {
      setCurrentModelId(modelId);
      setCurrentModelValue(modelValue);
      setModelSelectionError(null);
    },
    [],
  );

  const lockedAgentOptions: typeof currentAgentOptions = currentAgentOptions.enable_thinking
    ? { enable_thinking: currentAgentOptions.enable_thinking }
    : {};
  const lockedAgentOptionValues: typeof agentOptionValues =
    agentOptionValues.enable_thinking !== undefined
      ? { enable_thinking: agentOptionValues.enable_thinking }
      : {};

  // Sync ref synchronously during render so getAgentOptions always has
  // the latest model_id — useEffect introduces a one-tick delay that
  // can cause model_id to be missing when using the default model.
  sessionConfigRef.current = agentConversationControlsLocked
    ? {
        disabledSkills: [],
        selectedMcpToolIds: undefined,
        agentOptions: {
          ...lockedAgentOptionValues,
          ...(selectedGroupId ? { group_id: selectedGroupId } : {}),
          selection_mode: "explicit",
          ...(currentModelValue ? { model: currentModelValue } : {}),
          ...(currentModelId ? { model_id: currentModelId } : {}),
        },
      }
      : {
        ...sessionConfig,
        selectedMcpToolIds: sessionConfig.selectedMcpToolIds,
        agentOptions: {
          ...agentOptionValues,
          ...(selectedGroupId ? { group_id: selectedGroupId } : {}),
          selection_mode: "explicit",
          ...(currentModelValue ? { model: currentModelValue } : {}),
          ...(currentModelId ? { model_id: currentModelId } : {}),
        },
      };

  const effectiveTools = useMemo(() => {
    const selected = new Set(authoritativeMcpSelection);
    return tools.map((t) => {
      if (t.category !== "mcp") return t;
      return { ...t, enabled: selected.has(t.name) };
    });
  }, [authoritativeMcpSelection, tools]);

  const effectiveSkills = useMemo(() => {
    return buildEffectiveSkills({
      skills,
      skillsLoading,
      disabledSkillNames: sessionConfig.disabledSkills,
    });
  }, [
    skills,
    sessionConfig.disabledSkills,
    skillsLoading,
  ]);

  const effectiveToggleTool = useCallback(
    (toolName: string) => {
      if (!canSelectMcpTools) return;
      const tool = tools.find((t) => t.name === toolName);
      if (!tool) return;

      if (tool.category === "mcp") {
        toggleSessionMcpTool(toolName);
      }
    },
    [canSelectMcpTools, tools, toggleSessionMcpTool],
  );

  const effectiveToggleCategory = useCallback(
    (category: ToolCategory, enabled: boolean) => {
      if (canSelectMcpTools && category === "mcp") {
        setSelectedMcpToolIds(
          enabled
            ? tools
                .filter((t) => t.category === "mcp" && !t.system_disabled)
                .map((t) => t.name)
            : [],
        );
      }
    },
    [canSelectMcpTools, setSelectedMcpToolIds, tools],
  );

  const effectiveToggleAll = useCallback(
    (enabled: boolean) => {
      if (!canSelectMcpTools) return;
      setSelectedMcpToolIds(
        enabled
          ? tools
              .filter((t) => t.category === "mcp" && !t.system_disabled)
              .map((t) => t.name)
          : [],
      );
    },
    [canSelectMcpTools, setSelectedMcpToolIds, tools],
  );

  const effectiveEnabledToolsCount = useMemo(
    () => effectiveTools.filter((t) => t.enabled).length,
    [effectiveTools],
  );
  // ChatView's compatibility props are required, but undefined is the
  // established ChatInput signal that the MCP selector is unavailable.
  const exposedMcpControls = {
    onToggleTool: exposeGenericChatControl(agentConversationState.phase, effectiveToggleTool) as typeof effectiveToggleTool,
    onToggleCategory: exposeGenericChatControl(agentConversationState.phase, effectiveToggleCategory) as typeof effectiveToggleCategory,
    onToggleAll: exposeGenericChatControl(agentConversationState.phase, effectiveToggleAll) as typeof effectiveToggleAll,
  };

  const agentWorkspaceHistoryLoadEnabled = isExactAgentWorkspaceBinding({
    agentWorkspace,
    state: agentConversationState,
    sessionId: routeSessionId,
  });
  const agentWorkspaceTranscriptReady = isExactAgentWorkspaceBinding({
    agentWorkspace,
    state: agentConversationState,
    sessionId,
  });
  const visibleMessages = projectAgentWorkspaceTranscript({
    agentWorkspace,
    state: agentConversationState,
    sessionId,
    messages,
  });
  const visibleSessionId = agentWorkspaceTranscriptReady ? sessionId : null;
  const visibleCurrentRunId = agentWorkspaceTranscriptReady ? currentRunId : null;
  const recoveredSessionReady =
    agentConversationState.targetSessionId === null ||
    agentConversationState.targetSessionId === sessionId;
  const canSendMessage =
    hasPermission(Permission.CHAT_WRITE) &&
    !agentWorkspaceReadOnly &&
    agentConversationState.phase !== "loading" &&
    agentConversationState.phase !== "blocked" &&
    recoveredSessionReady &&
    (!agentWorkspace ||
      (!routeSessionId && !sessionId) ||
      agentWorkspaceTranscriptReady);

  const sidebarRef = useRef<SessionSidebarHandle>(null);
  const [threadListAdapter, setThreadListAdapter] = useState<ExternalStoreThreadListAdapter>();

  useWebSocketNotifications({
    sessionId,
    enabled: isAuthenticated,
    onSessionUnread: (sid, count) => {
      sidebarRef.current?.updateSessionUnread(sid, count);
    },
  });

  const [externalNavigationTargetRunId, setExternalNavigationTargetRunId] =
    useState<string | null>(null);
  const [
    externalNavigationTargetRunPending,
    setExternalNavigationTargetRunPending,
  ] = useState(false);
  const externalNavigationTargetFile = getExternalNavigationTargetFile(
    location.state,
  );
  const externalScrollToBottom = shouldScrollToBottomAfterExternalNavigation(
    location.state,
  );
  const externalNavigationToken =
    externalNavigationTargetFile || externalScrollToBottom
      ? location.key
      : null;

  useEffect(() => {
    const targetTraceId = externalNavigationTargetFile?.traceId ?? undefined;

    if (!sessionId || !targetTraceId) {
      setExternalNavigationTargetRunId(null);
      setExternalNavigationTargetRunPending(false);
      return;
    }

    let cancelled = false;
    setExternalNavigationTargetRunPending(true);

    const resolveTargetRunId = async () => {
      try {
        const { sessionApi } = await import("../../../services/api/session");
        const response = await sessionApi.getRuns(sessionId, {
          trace_id: targetTraceId,
        });
        if (cancelled) {
          return;
        }

        const matchedRun =
          response.runs.find((run) => run.trace_id === targetTraceId) ?? null;
        setExternalNavigationTargetRunId(matchedRun?.run_id ?? null);
        setExternalNavigationTargetRunPending(false);
      } catch (err) {
        if (!cancelled) {
          console.warn(
            "[AppContent] Failed to resolve external navigation run:",
            err,
          );
          setExternalNavigationTargetRunId(null);
          setExternalNavigationTargetRunPending(false);
        }
      }
    };

    resolveTargetRunId();

    return () => {
      cancelled = true;
    };
  }, [sessionId, externalNavigationTargetFile?.traceId]);

  const handleConfigRestored = useCallback(
    (config: {
      agent_options?: Record<string, boolean | string | number>;
      disabled_skills?: string[];
      disabled_mcp_tools?: string[];
      disabled_tools?: string[];
      selected_mcp_tool_ids?: string[];
    }) => {
      console.log("[AppContent] Restoring session config:", config);

      restoreSessionConfig(config);

      if (config.agent_options) {
        const restoredAgentOptions = { ...config.agent_options };
        delete restoredAgentOptions.model_id;
        delete restoredAgentOptions.model;
        delete restoredAgentOptions.group_id;
        delete restoredAgentOptions.selection_mode;
        restoreAgentOptions(restoredAgentOptions);

        const restoredModelSelection = getRestoredModelSelection(config);
        setModelSelectionError(null);
        if (restoredModelSelection.modelId) {
          setCurrentModelId(restoredModelSelection.modelId);
        }
        if (restoredModelSelection.modelValue) {
          setCurrentModelValue(restoredModelSelection.modelValue);
        }
      }
    },
    [restoreSessionConfig, restoreAgentOptions],
  );

  const { handleSelectSession, handleNewSession } = useSessionSync({
    activeTab: "chat",
    sessionId,
    loadHistory,
    clearMessages,
    onConfigRestored: handleConfigRestored,
    sessionRouteBasePath: agentWorkspaceRouteBasePath,
    historyLoadEnabled: agentWorkspaceHistoryLoadEnabled,
    routeOwnsSelection: Boolean(agentWorkspace),
  });

  const handleNewSessionWithReset = useCallback(() => {
    if (agentWorkspace) {
      resetAgentFirstSendTask();
      clearMessages();
      resetConversationForNewTask();
      navigate(agentWorkspaceRouteBasePath);
      return;
    }
    resetConversationForNewTask();
    handleNewSession();
    clearSelectedSkill();
    resetToDefaults();
    resetAgentOptionDefaults();

    setCurrentModelId("");
    setCurrentModelValue("");
    setModelSelectionError(null);
  }, [
    handleNewSession,
    clearSelectedSkill,
    resetToDefaults,
    resetAgentOptionDefaults,
    agentWorkspace,
    agentWorkspaceRouteBasePath,
    clearMessages,
    resetAgentFirstSendTask,
    resetConversationForNewTask,
    navigate,
  ]);

  const handleSendMessage = useCallback(
    async (
      content: string,
      options?: Record<string, boolean | string | number>,
      attachments?: MessageAttachment[],
      selectedSkill?: SelectedSkillRequest | null,
    ): Promise<SubmissionOutcome> => {
      clearAgentWorkspaceError();
      if (!selectedGroup || !availableModels?.length) {
        setModelSelectionError("当前没有可用业务组或模型，请联系管理员配置业务组。");
        return { status: "failed" };
      }
      if (!availableModels.some((model) => model.id === currentModelId && model.value === currentModelValue)) {
        setModelSelectionError("请先选择当前可用的模型。");
        return { status: "failed" };
      }
      if (!agentWorkspace || sessionId) {
        return sendMessage(content, options, attachments, selectedSkill);
      }
      return sendAgentFirstMessage(content, options, attachments);
    },
    [
      agentWorkspace,
      clearAgentWorkspaceError,
      sendAgentFirstMessage,
      sendMessage,
      sessionId,
      availableModels,
      currentModelId,
      currentModelValue,
      selectedGroup,
    ],
  );

  const handleMobileClose = useCallback(
    () => setMobileSidebarOpen(false),
    [setMobileSidebarOpen],
  );
  const handleSelectGlobalHistorySession = useCallback(
    (session: BackendSession) => {
      navigate(buildSessionHistoryPath(session.id, session.agent_conversation));
      setMobileSidebarOpen(false);
    },
    [navigate, setMobileSidebarOpen],
  );
  const handleSelectSessionAndClose = useCallback(
    async (id: string) => {
      if (agentWorkspace) {
        // Commit the intent immediately. The route owns identity recovery and
        // transcript loading, including a return to the previously open task.
        navigate(buildAgentMarketWorkspacePath(agentWorkspace, id));
      } else {
        beginConversationSelection(id);
        clearMessages();
        clearSelectedSkill();
        await handleSelectSession(id);
      }
      setMobileSidebarOpen(false);
    },
    [agentWorkspace, beginConversationSelection, clearMessages, clearSelectedSkill, handleSelectSession, navigate, setMobileSidebarOpen],
  );
  const handleNewSessionAndClose = useCallback(() => {
    handleNewSessionWithReset();
    setMobileSidebarOpen(false);
  }, [handleNewSessionWithReset, setMobileSidebarOpen]);

  const outlineToggleRef = useRef<(() => void) | null>(null);
  const handleToggleOutline = useCallback(() => {
    outlineToggleRef.current?.();
  }, []);

  const handleOpenRunPlayback = useCallback(() => {
    if (!currentRunId) return;
    const panelKey = `run-playback:${currentRunId}`;
    const isMobile = typeof window !== "undefined" && window.innerWidth < 640;
    runControlLifecycle.open();
    openPersistentToolPanel({
      title: t("runPlayback.title"),
      icon: <History size={16} />,
      status: "loading",
      subtitle: currentRunId,
      panelKey,
      viewMode: isMobile ? "center" : "sidebar",
      mobileFillViewport: true,
      children: <RunPlaybackPanel lifecycle={runControlLifecycle} panelKey={panelKey} />,
    });
  }, [currentRunId, runControlLifecycle, t]);

  return (
    <AppShell
      activeTab="chat"
      onNewSession={handleNewSessionWithReset}
      newSessionActionLabel={agentWorkspace ? "开始新任务" : undefined}
      currentRunId={visibleCurrentRunId}
      onOpenRunPlayback={handleOpenRunPlayback}
      showOutlineButton={shouldShowMessageOutline(visibleMessages)}
      onToggleOutline={handleToggleOutline}
      sidebar={
        <SessionSidebar
          ref={sidebarRef}
          onThreadListAdapterChange={setThreadListAdapter}
          currentSessionId={sessionId}
          onSelectSession={handleSelectSessionAndClose}
          onSelectGlobalHistorySession={
            agentWorkspace ? handleSelectGlobalHistorySession : undefined
          }
          onNewSession={handleNewSessionAndClose}
          newSession={newlyCreatedSession}
          mobileOpen={mobileSidebarOpen}
          onMobileOpen={() => setMobileSidebarOpen(true)}
          onMobileClose={handleMobileClose}
          isCollapsed={sidebarCollapsed}
          onToggleCollapsed={setSidebarCollapsed}
          sessionFilter={
            agentWorkspace && !agentWorkspaceSessionSource
              ? () => false
              : undefined
          }
          sessionSource={agentWorkspaceSessionSource}
          globalHistorySource={globalHistorySource}
          agentWorkspace={
            agentWorkspace
              ? {
                  agent_id: agentWorkspace.agent_id,
                  avatar_ref: agentWorkspace.avatar_ref,
                  avatar_seed: agentWorkspace.avatar_seed,
                  name: agentWorkspace.name,
                  description: agentWorkspace.description,
                }
              : undefined
          }
          navigationOnly={false}
        />
      }
    >
      <div className="min-h-0 flex-1 overflow-hidden flex flex-col">
        {isPageDragging && (
          <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-neutral-500/5 transition-colors dark:bg-neutral-500/10">
            <div className="flex flex-col items-center gap-3 rounded-lg border-2 border-dashed border-neutral-400 bg-[var(--theme-bg-card)] px-16 py-12 shadow-[0_12px_28px_rgba(18,38,63,0.08)] transition-colors dark:border-neutral-500 dark:bg-neutral-900">
              <svg
                xmlns="http://www.w3.org/2000/svg"
                className="h-12 w-12 text-neutral-500 dark:text-neutral-400"
                fill="none"
                viewBox="0 0 24 24"
                strokeWidth={1.5}
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5"
                />
              </svg>
              <span className="text-lg font-medium text-neutral-600 dark:text-neutral-300">
                {t("chat.dropFilesHere", "Drop files here to upload")}
              </span>
            </div>
          </div>
        )}

        {agentConversationState.phase === "loading" &&
        agentConversationState.targetSessionId ? (
          <div
            aria-live="polite"
            className="border-b border-[var(--theme-border)] px-4 py-3 text-center text-sm text-[var(--theme-text-secondary)]"
            data-agent-conversation-loading
          >
            正在校验会话身份…
          </div>
        ) : null}
        {agentWorkspace && agentWorkspaceReadOnly ? (
          <div
            aria-live="polite"
            className="border-b border-[var(--theme-warning-ring)] bg-[var(--theme-warning-soft)] px-4 py-3 text-center text-sm text-[var(--theme-warning)]"
            data-agent-workspace-readonly
            role="status"
          >
            该专家已撤回或当前账号无权使用，此历史会话只读。
          </div>
        ) : null}
        {failureGuidance ? (
          <div className="border-b border-[var(--theme-border)] px-4 py-3">
            <FailureGuidanceCard guidance={failureGuidance} />
          </div>
        ) : null}
        <ChatMcpCatalogContext.Provider value={mcpCatalogContextValue}>
            <ChatView
            threadListAdapter={threadListAdapter}
            messages={visibleMessages}
            sessionId={visibleSessionId}
            conversationIdentityKey={conversationIdentityKey}
            currentRunId={visibleCurrentRunId}
            runInputs={agentWorkspaceTranscriptReady ? runInputs : undefined}
            canStopGeneration={
              agentWorkspaceTranscriptReady && canStopGeneration
            }
            isLoading={isLoading}
            isStopping={agentWorkspaceTranscriptReady && isStopping}
            stopUnconfirmed={agentWorkspaceTranscriptReady && stopUnconfirmed}
            isLoadingHistory={isLoadingHistory}
            historyReadError={agentWorkspaceTranscriptReady ? historyReadError : null}
            onRetryHistoryLoad={retryHistoryLoad}
            connectionStatus={connectionStatus}
            canSendMessage={canSendMessage}
            continuation={agentWorkspaceTranscriptReady ? continuation : undefined}
            continuationPending={agentWorkspaceTranscriptReady && continuationPending}
            modelUnavailableReason={modelsLoading && !currentModelId ? "正在加载模型…" : modelSelectionError
              ? modelSelectionError === "所属模型组不可用或归属异常，请联系管理员。"
                ? "当前没有可用模型，请联系管理员申请。" : modelSelectionError
              : !currentModelId ? "暂时无法读取可用模型，请重试。" : undefined}
            initialComposerDraft={agentWorkspaceStarterDraft}
            initialComposerDraftKey={location.key}
            composerDraftHandoffKey={
              agentWorkspace
                ? agentWorkspaceDraftHandoffKey
                : newlyCreatedSession?.id ?? null
            }
            presentation={agentChatPresentation}
            composerPlaceholder={
              agentWorkspaceReadOnly
                ? "该历史会话为只读状态"
                : agentWorkspace
                ? `向 ${agentWorkspace.name} 描述要完成的任务…`
                : "提问、上传文件，或描述你想完成的事…"
            }
            tools={agentConversationControlsLocked ? [] : effectiveTools}
            onToggleTool={exposedMcpControls.onToggleTool}
            onToggleCategory={exposedMcpControls.onToggleCategory}
            onToggleAll={exposedMcpControls.onToggleAll}
            enabledToolsCount={
              agentConversationControlsLocked ? 0 : effectiveEnabledToolsCount
            }
            totalToolsCount={agentConversationControlsLocked ? 0 : totalToolsCount}
            skills={agentConversationControlsLocked ? [] : effectiveSkills}
            taskSkills={agentConversationControlsLocked ? [] : skills}
            selectedSkillState={
              agentConversationControlsLocked
                ? LOCKED_SELECTED_SKILL_STATE
                : selectedSkillState
            }
            onSelectSkill={selectSkill}
            onClearSelectedSkill={clearSelectedSkill}
            onSelectedSkillRecoverable={recoverSelectedSkill}
            onSelectedSkillFilesReady={markSelectedSkillFilesReady}
            skillsLoading={agentConversationControlsLocked ? false : skillsLoading}
            enabledSkillsCount={
              agentConversationControlsLocked
                ? 0
                : countEnabledSkills(effectiveSkills)
            }
            totalSkillsCount={
              agentConversationControlsLocked ? 0 : effectiveSkills.length
            }
            enableSkills={
              !agentConversationControlsLocked &&
              composerSkillsAvailability.enableComposerSkills
            }
            agentOptions={
              agentConversationControlsLocked ? lockedAgentOptions : currentAgentOptions
            }
            agentOptionValues={
              agentConversationControlsLocked
                ? lockedAgentOptionValues
                : agentOptionValues
            }
            onToggleAgentOption={handleToggleAgentOption}
            availableModels={filteredModels ?? []}
            currentModelId={currentModelId}
            onSelectModel={handleSelectModel}
            onModelSelectorOpen={refreshModels}
            onSendMessage={handleSendMessage}
            canRetryPendingSubmission={canRetryPendingSubmission}
            onRetryPendingSubmission={retryPendingSubmission}
            onStopGeneration={stopGeneration}
            onReconnect={reconnectSSE}
            onLoadHistory={() =>
              sessionId ? loadHistory(sessionId) : Promise.resolve(null)
            }
            attachments={pageDragAttachments}
            onAttachmentsChange={setPageDragAttachments}
            uploadControls={uploadControls}
            externalNavigationToken={externalNavigationToken}
            externalNavigationTargetFile={externalNavigationTargetFile}
            externalNavigationTargetRunId={externalNavigationTargetRunId}
            externalNavigationTargetRunPending={
              externalNavigationTargetRunPending
            }
            externalScrollToBottom={externalScrollToBottom}
            outlineToggleRef={outlineToggleRef}
            WorkbenchShellComponent={WorkbenchShell}
            />
          </ChatMcpCatalogContext.Provider>
        {agentWorkspaceError ? (
          <p className="px-4 pb-2 text-center text-sm text-[var(--theme-danger-text)]" role="alert">
            {agentWorkspaceError}
          </p>
        ) : null}
        {agentWorkspaceHistoryError ? (
          <div className="flex items-center justify-center gap-3 px-4 pb-2 text-sm text-[var(--theme-warning)]">
            <span>历史会话暂时无法加载。</span>
            {onAgentWorkspaceHistoryRetry ? (
              <Button type="text" className="underline" onClick={onAgentWorkspaceHistoryRetry} htmlType="button">
                重试
              </Button>
            ) : null}
          </div>
        ) : null}
        <BlockPreviewPortal />
      </div>
    </AppShell>
  );
}
