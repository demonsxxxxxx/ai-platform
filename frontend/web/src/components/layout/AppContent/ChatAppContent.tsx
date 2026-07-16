import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router-dom";
import { History } from "lucide-react";
import { BlockPreviewPortal } from "../../chat/ChatMessage/items/McpBlockPreview";
import { SessionSidebar } from "../../panels/SessionSidebar";
import type { SessionSidebarHandle } from "../../panels/SessionSidebar";
import { useSettingsContext } from "../../../contexts/SettingsContext";
import { useAgent } from "../../../hooks/useAgent";
import { useApprovals } from "../../../hooks/useApprovals";
import { useAuth } from "../../../hooks/useAuth";
import { useTools } from "../../../hooks/useTools";
import { useSkills } from "../../../hooks/useSkills";
import { useSelectedSkillTask } from "../../../hooks/useSelectedSkillTask";
import { useProjectManager } from "../../../hooks/useProjectManager";
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
    tools,
    isLoading: toolsLoading,
    totalCount: totalToolsCount,
    getDisabledToolNames,
  } = useTools({ enabled: true });

  const {
    skills,
    isLoading: skillsLoading,
    listError: skillsListError,
    fetchSkills,
    effectivePermissions: skillsEffectivePermissions,
    effectivePermissionsKnown: skillsEffectivePermissionsKnown,
  } = useSkills({
    enabled: composerSkillsProbeAvailability.shouldFetchSkills,
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

  const projectManager = useProjectManager();

  const sessionConfigRef = useRef({
    disabledSkills: [] as string[],
    disabledMcpTools: [] as string[],
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
    currentProjectId,
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
    getEnabledTools: getDisabledToolNames,
    getDisabledSkills: () => sessionConfigRef.current.disabledSkills,
    getDisabledMcpTools: () => sessionConfigRef.current.disabledMcpTools,
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
    setAgentOption: setSessionAgentOption,
    resetToDefaults,
    restoreConfig: restoreSessionConfig,
  } = useSessionConfig({
    getDefaultAgentOptions: () => agentOptionValues,
  });

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
  sessionConfigRef.current = {
    ...sessionConfig,
    agentOptions: {
      ...agentOptionValues,
      ...(currentModelValue ? { model: currentModelValue } : {}),
      ...(currentModelId ? { model_id: currentModelId } : {}),
    },
  };

  const effectiveTools = useMemo(() => {
    const sessionDisabled = new Set(sessionConfig.disabledMcpTools);
    if (sessionDisabled.size === 0) return tools;
    return tools.map((t) => {
      if (t.category !== "mcp") return t;
      return { ...t, enabled: t.enabled && !sessionDisabled.has(t.name) };
    });
  }, [tools, sessionConfig.disabledMcpTools]);

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
      const tool = tools.find((t) => t.name === toolName);
      if (!tool) return;

      if (tool.category === "mcp") {
        toggleSessionMcpTool(toolName);
      }
    },
    [tools, toggleSessionMcpTool],
  );

  const effectiveToggleCategory = useCallback(
    (category: ToolCategory, enabled: boolean) => {
      if (category === "mcp") {
        tools
          .filter((t) => t.category === "mcp" && !t.system_disabled)
          .forEach((t) => {
            const isInSessionDisabled = sessionConfig.disabledMcpTools.includes(
              t.name,
            );
            if (enabled && isInSessionDisabled) {
              toggleSessionMcpTool(t.name);
            } else if (!enabled && !isInSessionDisabled) {
              toggleSessionMcpTool(t.name);
            }
          });
      }
    },
    [tools, sessionConfig.disabledMcpTools, toggleSessionMcpTool],
  );

  const effectiveToggleAll = useCallback(
    (enabled: boolean) => {
      tools
        .filter((t) => t.category === "mcp" && !t.system_disabled)
        .forEach((t) => {
          const isInSessionDisabled = sessionConfig.disabledMcpTools.includes(
            t.name,
          );
          if (enabled && isInSessionDisabled) {
            toggleSessionMcpTool(t.name);
          } else if (!enabled && !isInSessionDisabled) {
            toggleSessionMcpTool(t.name);
          }
        });
    },
    [tools, sessionConfig.disabledMcpTools, toggleSessionMcpTool],
  );

  const effectiveEnabledToolsCount = useMemo(
    () => effectiveTools.filter((t) => t.enabled).length,
    [effectiveTools],
  );

  const canSendMessage = hasPermission(Permission.CHAT_WRITE);

  const sidebarRef = useRef<SessionSidebarHandle>(null);

  useWebSocketNotifications({
    sessionId,
    enabled: isAuthenticated,
    onSessionUnread: (sid, count, projectId, isFavorite) => {
      sidebarRef.current?.updateSessionUnread(
        sid,
        count,
        projectId,
        isFavorite,
      );
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
    openPersistentToolPanel({
      title: t("runPlayback.title"),
      icon: <History size={16} />,
      status: "loading",
      subtitle: currentRunId,
      panelKey,
      viewMode: isMobile ? "center" : "sidebar",
      mobileFillViewport: true,
      children: <RunPlaybackPanel runId={currentRunId} panelKey={panelKey} />,
    });
  }, [currentRunId, t]);

  return (
    <AppShell
      activeTab="chat"
      setMobileSidebarOpen={setMobileSidebarOpen}
      currentProjectId={currentProjectId}
      projectManager={projectManager}
      onNewSession={handleNewSessionWithReset}
      availableModels={filteredModels}
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

        <ChatView
          messages={messages}
          sessionId={sessionId}
          currentRunId={currentRunId}
          isLoading={isLoading}
          isLoadingHistory={isLoadingHistory}
          connectionStatus={connectionStatus}
          canSendMessage={canSendMessage}
          tools={effectiveTools}
          onToggleTool={effectiveToggleTool}
          onToggleCategory={effectiveToggleCategory}
          onToggleAll={effectiveToggleAll}
          toolsLoading={toolsLoading}
          enabledToolsCount={effectiveEnabledToolsCount}
          totalToolsCount={totalToolsCount}
          skills={effectiveSkills}
          taskSkills={skills}
          selectedSkillState={selectedSkillState}
          onSelectSkill={selectSkill}
          onClearSelectedSkill={clearSelectedSkill}
          onSelectedSkillRecoverable={recoverSelectedSkill}
          onSelectedSkillFilesReady={markSelectedSkillFilesReady}
          skillsLoading={skillsLoading}
          enabledSkillsCount={countEnabledSkills(effectiveSkills)}
          totalSkillsCount={effectiveSkills.length}
          enableSkills={composerSkillsAvailability.enableComposerSkills}
          agentOptions={currentAgentOptions}
          agentOptionValues={agentOptionValues}
          onToggleAgentOption={handleToggleAgentOption}
          availableModels={filteredModels ?? []}
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
        <BlockPreviewPortal />
      </>
    </AppShell>
  );
}
