/* eslint-disable react-refresh/only-export-components -- behavioral seams stay with the canonical Chat owner */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { Bot, FileText, Headphones, History, Search } from "lucide-react";
import { BlockPreviewPortal } from "../../chat/ChatMessage/items/McpBlockPreview";
import { SessionSidebar } from "../../panels/SessionSidebar";
import type { SessionSidebarHandle } from "../../panels/SessionSidebar";
import { useSettingsContext } from "../../../contexts/SettingsContext";
import { useAgent } from "../../../hooks/useAgent";
import { useApprovals } from "../../../hooks/useApprovals";
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
  type ToolCategory,
} from "../../../types";
import { useDragAndDrop } from "./useDragAndDrop";
import { useWebSocketNotifications } from "./useWebSocketNotifications";
import { useAgentOptions } from "./useAgentOptions";
import { useSessionSync } from "./useSessionSync";
import {
  getExternalNavigationTargetFile,
  shouldScrollToBottomAfterExternalNavigation,
} from "./externalNavigationState";
import {
  reconcileCurrentModelSelection,
  resolveDefaultModelSelection,
} from "./modelSelection";
import { getRestoredModelSelection } from "./sessionState";
import {
  buildEffectiveSkills,
  countEnabledSkills,
  resolveComposerSkillsAvailability,
  resolveSettingsBooleanProjection,
} from "./skillAvailability";
import { AppShell } from "./AppShell";
import { ChatView } from "./ChatView";
import { WorkbenchShell } from "../../workbench/WorkbenchShell";
import { CHAT_AGENT_OPTION_DEFINITIONS } from "../../../types/agentOptions";
import { shouldShowMessageOutline } from "./messageOutline";
import { RunPlaybackPanel } from "./RunPlaybackPanel";
import { openPersistentToolPanel } from "../../chat/ChatMessage/items/persistentToolPanelState";
import { agentProfileApi } from "../../../services/api/agentProfile";
import { sessionApi } from "../../../services/api/session";
import type {
  AgentConversationIdentity,
  AgentProfileAvatarRef,
  AgentProfileCategory,
} from "../../../types/agentProfile";

export type AgentConversationRecoveryPhase = "generic" | "loading" | "bound" | "blocked";

interface AgentConversationRecoveryState {
  phase: AgentConversationRecoveryPhase;
  targetSessionId: string | null;
  identity: AgentConversationIdentity | null;
}

function conversationState(
  phase: AgentConversationRecoveryPhase,
  targetSessionId: string | null,
  identity: AgentConversationIdentity | null = null,
): AgentConversationRecoveryState {
  return { phase, targetSessionId, identity };
}

const AGENT_CATEGORY_LABELS: Record<AgentProfileCategory, string> = {
  general: "通用助理", support: "支持服务", writing: "内容写作",
  research: "研究分析", operations: "运营效率",
};

const LOCKED_SELECTED_SKILL_STATE: SelectedSkillTaskState = {
  selectedSkill: null, status: "idle", recoveryCode: null, requiresReconfirmation: false,
};

/** A bound or unresolved Session must not expose client capability controls. */
export function areAgentConversationControlsLocked(
  phase: AgentConversationRecoveryPhase,
): boolean {
  return phase !== "generic";
}

/** Remove a client capability control until the Session is proven generic. */
export function exposeGenericChatControl<T>(
  phase: AgentConversationRecoveryPhase,
  control: T,
): T | undefined {
  return areAgentConversationControlsLocked(phase) ? undefined : control;
}

/** Recover and revalidate one server-owned Agent Conversation identity. */
export async function recoverAgentConversationIdentity(
  sessionId: string,
): Promise<AgentConversationIdentity | null> {
  const session = await sessionApi.getAuthoritative(sessionId);
  const identity = session.agent_conversation;
  if (session.session_id !== sessionId)
    throw new Error("agent_conversation_identity_mismatch");
  if (identity === null) return null;
  if (session.agent_id !== identity.agent_id)
    throw new Error("agent_conversation_identity_mismatch");
  const currentProfile = await agentProfileApi.getPublished(identity.agent_id);
  if (
    currentProfile.agent_id !== identity.agent_id ||
    currentProfile.expected_revision !== identity.revision
  )
    throw new Error("agent_conversation_revision_mismatch");
  return identity;
}

function AgentConversationAvatar({ avatarRef }: { avatarRef: AgentProfileAvatarRef }) {
  const iconProps = { size: 22, "aria-hidden": true } as const;
  if (avatarRef === "builtin:assistant") return <Headphones {...iconProps} />;
  if (avatarRef === "builtin:document") return <FileText {...iconProps} />;
  if (avatarRef === "builtin:research") return <Search {...iconProps} />;
  return <Bot {...iconProps} />;
}

/** Render only the public immutable Agent identity above canonical Chat. */
export function AgentConversationIdentityBanner({
  identity,
}: {
  identity: AgentConversationIdentity;
}) {
  return (
    <section
      data-agent-conversation-profile
      className="border-b border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-4 py-3 text-[var(--theme-text)] sm:px-6"
    >
      <div className="mx-auto flex max-w-4xl items-center gap-3">
        <span
          aria-label={`${identity.name} 头像`}
          className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-emerald-100 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300"
          data-agent-avatar-ref={identity.avatar_ref}
          role="img"
        >
          <AgentConversationAvatar avatarRef={identity.avatar_ref} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <strong className="text-sm font-semibold sm:text-base">{identity.name}</strong>
            <span className="text-xs text-[var(--theme-text-secondary)]">
              {AGENT_CATEGORY_LABELS[identity.category]}
            </span>
          </span>
          {identity.description ? (
            <span className="mt-1 block line-clamp-2 text-xs leading-5 text-[var(--theme-text-secondary)] sm:text-sm">
              {identity.description}
            </span>
          ) : null}
        </span>
      </div>
    </section>
  );
}

export interface ChatAppContentProps {
  sidebarCollapsed: boolean;
  setSidebarCollapsed: (collapsed: boolean) => void;
  mobileSidebarOpen: boolean;
  setMobileSidebarOpen: (open: boolean) => void;
}

export function ChatAppContent({
  sidebarCollapsed,
  setSidebarCollapsed,
  mobileSidebarOpen,
  setMobileSidebarOpen,
}: ChatAppContentProps) {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const { sessionId: routeSessionId } = useParams<{ sessionId?: string }>();
  const [agentConversationState, setAgentConversationState] = useState(() =>
    conversationState(routeSessionId ? "loading" : "generic", routeSessionId ?? null),
  );
  const agentConversationControlsLocked =
    areAgentConversationControlsLocked(agentConversationState.phase);
  const { enableSkills, settings, availableModels, defaultModel } =
    useSettingsContext();
  const { hasPermission, isAuthenticated } = useAuth();
  const canReadSkills = hasPermission(Permission.SKILL_READ);
  const enableSkillsProjection = resolveSettingsBooleanProjection(
    settings,
    "ENABLE_SKILLS",
  );
  const composerSkillsProbeAvailability = resolveComposerSkillsAvailability({
    isAuthenticated,
    canReadSkills,
    catalogEffectivePermissions: [],
    catalogPermissionsKnown: false,
    enableSkillsSettingKnown: enableSkillsProjection.known,
    enableSkillsSetting: enableSkillsProjection.value ?? enableSkills,
  });

  const { isPageDragging, pageDragAttachments, setPageDragAttachments } =
    useDragAndDrop();

  const {
    approvals,
    respondToApproval,
    addApproval,
    clearApprovals,
    isLoading: approvalLoading,
  } = useApprovals({ sessionId: null });

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
    canReadSkills,
    catalogEffectivePermissions: skillsEffectivePermissions,
    catalogPermissionsKnown: skillsEffectivePermissionsKnown,
    enableSkillsSettingKnown: enableSkillsProjection.known,
    enableSkillsSetting: enableSkillsProjection.value ?? enableSkills,
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
    isLoading,
    isLoadingHistory,
    connectionStatus,
    newlyCreatedSession,
    sendMessage,
    canRetryPendingSubmission,
    retryPendingSubmission,
    stopGeneration,
    clearMessages,
    loadHistory,
    runControlLifecycle,
  } = useAgent({
    onApprovalRequired: (approval) => {
      addApproval({
        id: approval.id,
        message: approval.message,
        type: "form",
        fields: approval.fields || [],
        status: "pending",
        session_id: sessionId,
      });
    },
    onClearApprovals: () => {
      clearApprovals();
    },
    getDisabledSkills: () => sessionConfigRef.current.disabledSkills,
    // The legacy callback type says ``string[]``. The runtime deliberately
    // preserves ``undefined`` so an omitted selection can inherit from the
    // authoritative server session.
    getDisabledMcpTools: () =>
      sessionConfigRef.current.selectedMcpToolIds as string[],
    getAgentOptions: () => sessionConfigRef.current.agentOptions,
    onSkillAdded: (
      skillName: string,
      _description: string,
      filesCount: number,
    ) => {
      console.log(
        `[AppContent] Skill added: ${skillName} (${filesCount} files), refreshing skills list`,
      );
      setTimeout(() => fetchSkills(), 500);
    },
  });

  const agentConversationTargetSessionId = routeSessionId ?? sessionId;
  useEffect(() => {
    if (!agentConversationTargetSessionId) {
      setAgentConversationState(conversationState("generic", null));
      return;
    }

    let active = true;
    setAgentConversationState(conversationState("loading", agentConversationTargetSessionId));
    void recoverAgentConversationIdentity(agentConversationTargetSessionId)
      .then((identity) => {
        if (!active) return;
        setAgentConversationState(
          conversationState(
            identity ? "bound" : "generic",
            agentConversationTargetSessionId,
            identity,
          ),
        );
      })
      .catch(() => {
        if (!active) return;
        setAgentConversationState(
          conversationState("blocked", agentConversationTargetSessionId),
        );
        navigate("/agent-market", { replace: true });
      });
    return () => {
      active = false;
    };
  }, [agentConversationTargetSessionId, navigate]);

  const {
    tools,
    serverSelectedToolIds,
    isLoading: toolsLoading,
    totalCount: totalToolsCount,
    catalogState: mcpCatalogState,
    refreshTools,
  } = useTools({
    enabled: !agentConversationControlsLocked,
    sessionId: agentConversationControlsLocked ? null : sessionId,
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
        ? reconcileChatMcpToolSelection(
            serverSelectedToolIds,
            tools,
            mcpCatalogState.status,
          )
        : undefined;
    const restoreKey =
      serverSelection === undefined ? null : `${sessionId}:${JSON.stringify(serverSelection)}`;
    const shouldRestoreServerSelection =
      restoreKey !== null && restoredMcpSelectionRef.current !== restoreKey;
    if (shouldRestoreServerSelection) {
      restoredMcpSelectionRef.current = restoreKey;
    }

    const reconciled = shouldRestoreServerSelection
      ? serverSelection
      : reconcileChatMcpToolSelection(
          sessionConfig.selectedMcpToolIds,
          tools,
          mcpCatalogState.status,
        );
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

  const [currentModelId, setCurrentModelId] = useState<string>(() => {
    return localStorage.getItem("defaultModelId") || "";
  });
  const [currentModelValue, setCurrentModelValue] = useState<string>(
    () => localStorage.getItem("defaultModel") || defaultModel,
  );

  const isSessionRestoredRef = useRef(false);

  useEffect(() => {
    if (isSessionRestoredRef.current) return;
    const nextSelection = reconcileCurrentModelSelection({
      availableModels,
      currentModelId,
      currentModelValue,
      storedDefaultId: localStorage.getItem("defaultModelId") || "",
      storedDefaultValue: localStorage.getItem("defaultModel") || "",
      fallbackDefaultValue: defaultModel,
    });

    if (nextSelection.modelId && nextSelection.modelId !== currentModelId) {
      setCurrentModelId(nextSelection.modelId);
    }
    if (
      nextSelection.modelValue &&
      nextSelection.modelValue !== currentModelValue
    ) {
      setCurrentModelValue(nextSelection.modelValue);
    }
  }, [availableModels, currentModelId, currentModelValue, defaultModel]);

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
    },
    [],
  );

  // Sync ref synchronously during render so getAgentOptions always has
  // the latest model_id — useEffect introduces a one-tick delay that
  // can cause model_id to be missing when using the default model.
  sessionConfigRef.current = agentConversationControlsLocked
    ? {
        disabledSkills: [],
        selectedMcpToolIds: undefined,
        agentOptions: {},
      }
      : {
        ...sessionConfig,
        selectedMcpToolIds: authoritativeMcpSelection,
        agentOptions: {
          ...agentOptionValues,
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

  const recoveredSessionReady =
    agentConversationState.targetSessionId === null ||
    agentConversationState.targetSessionId === sessionId;
  const canSendMessage =
    hasPermission(Permission.CHAT_WRITE) &&
    agentConversationState.phase !== "loading" &&
    agentConversationState.phase !== "blocked" &&
    recoveredSessionReady;

  const sidebarRef = useRef<SessionSidebarHandle>(null);

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

      isSessionRestoredRef.current = true;

      restoreSessionConfig(config);

      if (config.agent_options) {
        restoreAgentOptions(config.agent_options);

        const restoredModelSelection = getRestoredModelSelection(config);
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
  });

  const handleNewSessionWithReset = useCallback(() => {
    const nextSelection = resolveDefaultModelSelection({
      availableModels,
      storedDefaultId: localStorage.getItem("defaultModelId") || "",
      storedDefaultValue: localStorage.getItem("defaultModel") || "",
      fallbackDefaultValue: defaultModel,
    });

    setAgentConversationState(conversationState("generic", null));
    handleNewSession();
    clearSelectedSkill();
    resetToDefaults();

    resetAgentOptionDefaults();

    setCurrentModelId(nextSelection.modelId);
    setCurrentModelValue(nextSelection.modelValue);
  }, [
    availableModels,
    defaultModel,
    handleNewSession,
    clearSelectedSkill,
    resetToDefaults,
    resetAgentOptionDefaults,
  ]);

  const handleMobileClose = useCallback(
    () => setMobileSidebarOpen(false),
    [setMobileSidebarOpen],
  );
  const handleSelectSessionAndClose = useCallback(
    (id: string) => {
      setAgentConversationState(conversationState("loading", id));
      clearSelectedSkill();
      handleSelectSession(id);
      setMobileSidebarOpen(false);
    },
    [clearSelectedSkill, handleSelectSession, setMobileSidebarOpen],
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
      setMobileSidebarOpen={setMobileSidebarOpen}
      onNewSession={handleNewSessionWithReset}
      availableModels={agentConversationControlsLocked ? null : filteredModels}
      currentModelId={currentModelId}
      onSelectModel={handleSelectModel}
      sessionId={sessionId}
      currentRunId={currentRunId}
      onOpenRunPlayback={handleOpenRunPlayback}
      showOutlineButton={shouldShowMessageOutline(messages)}
      onToggleOutline={handleToggleOutline}
      sidebar={
        <SessionSidebar
          ref={sidebarRef}
          currentSessionId={sessionId}
          onSelectSession={handleSelectSessionAndClose}
          onNewSession={handleNewSessionAndClose}
          newSession={newlyCreatedSession}
          mobileOpen={mobileSidebarOpen}
          onMobileOpen={() => setMobileSidebarOpen(true)}
          onMobileClose={handleMobileClose}
          isCollapsed={sidebarCollapsed}
          onToggleCollapsed={setSidebarCollapsed}
        />
      }
    >
      <>
        {isPageDragging && (
          <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-stone-500/5 transition-colors dark:bg-stone-500/10">
            <div className="flex flex-col items-center gap-3 rounded-lg border-2 border-dashed border-stone-400 bg-[var(--theme-bg-card)] px-16 py-12 shadow-[0_12px_28px_rgba(18,38,63,0.08)] transition-colors dark:border-stone-500 dark:bg-stone-900">
              <svg
                xmlns="http://www.w3.org/2000/svg"
                className="h-12 w-12 text-stone-500 dark:text-stone-400"
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
              <span className="text-lg font-medium text-stone-600 dark:text-stone-300">
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
        {agentConversationState.phase === "bound" &&
        agentConversationState.identity ? (
          <AgentConversationIdentityBanner
            identity={agentConversationState.identity}
          />
        ) : null}

        <ChatMcpCatalogContext.Provider value={mcpCatalogContextValue}>
          <ChatView
            messages={messages}
            sessionId={sessionId}
            currentRunId={currentRunId}
            isLoading={isLoading}
            isLoadingHistory={isLoadingHistory}
            connectionStatus={connectionStatus}
            canSendMessage={canSendMessage}
            tools={agentConversationControlsLocked ? [] : effectiveTools}
            onToggleTool={exposedMcpControls.onToggleTool}
            onToggleCategory={exposedMcpControls.onToggleCategory}
            onToggleAll={exposedMcpControls.onToggleAll}
            toolsLoading={agentConversationControlsLocked ? false : toolsLoading}
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
            agentOptions={agentConversationControlsLocked ? {} : currentAgentOptions}
            agentOptionValues={
              agentConversationControlsLocked ? {} : agentOptionValues
            }
            onToggleAgentOption={handleToggleAgentOption}
            availableModels={
              agentConversationControlsLocked ? [] : filteredModels ?? []
            }
            currentModelId={currentModelId}
            onSelectModel={handleSelectModel}
            approvals={approvals}
            onRespondApproval={respondToApproval}
            approvalLoading={approvalLoading}
            onSendMessage={sendMessage}
            canRetryPendingSubmission={canRetryPendingSubmission}
            onRetryPendingSubmission={retryPendingSubmission}
            onStopGeneration={stopGeneration}
            attachments={pageDragAttachments}
            onAttachmentsChange={setPageDragAttachments}
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
        <BlockPreviewPortal />
      </>
    </AppShell>
  );
}
