/* eslint-disable react-refresh/only-export-components -- behavioral seams stay with the canonical Chat owner */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import toast from "react-hot-toast";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { ArrowRight, Bot, FileText, Headphones, History, MessageCircle, Search, ShieldCheck } from "lucide-react";
import { BlockPreviewPortal } from "../../chat/ChatMessage/items/McpBlockPreview";
import { SessionSidebar } from "../../panels/SessionSidebar";
import type { SessionSidebarHandle } from "../../panels/SessionSidebar";
import type { SessionSidebarSessionSource } from "../../panels/SessionSidebar";
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
  AgentProfilePublicProjection,
} from "../../../types/agentProfile";

export type AgentConversationRecoveryPhase = "generic" | "loading" | "bound" | "blocked";

export interface AgentConversationRecoveryState {
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

interface AgentWorkspaceBindingInput {
  agentWorkspace?: Pick<
    AgentProfilePublicProjection,
    "agent_id" | "expected_revision"
  >;
  state: AgentConversationRecoveryState;
  sessionId: string | null | undefined;
}

/** Accept Agent transcript data only after its Session and immutable revision agree. */
export function isExactAgentWorkspaceBinding({
  agentWorkspace,
  state,
  sessionId,
}: AgentWorkspaceBindingInput): boolean {
  if (!agentWorkspace) {
    return true;
  }

  return Boolean(
    sessionId &&
      state.phase === "bound" &&
      state.targetSessionId === sessionId &&
      state.identity?.agent_id === agentWorkspace.agent_id &&
      state.identity.revision === agentWorkspace.expected_revision,
  );
}

/** Keep a prior Session transcript out of an Agent workspace until its binding is exact. */
export function projectAgentWorkspaceTranscript<T>({
  messages,
  ...binding
}: AgentWorkspaceBindingInput & { messages: T[] }): T[] {
  return isExactAgentWorkspaceBinding(binding) ? messages : [];
}

/** Generic Chat retains tool selection; an Agent workspace never receives it. */
export function getChatToolAccess({
  agentWorkspace,
  phase,
  sessionId,
}: {
  agentWorkspace?: Pick<AgentProfilePublicProjection, "agent_id" | "expected_revision">;
  phase: AgentConversationRecoveryPhase;
  sessionId: string | null;
}): { enabled: boolean; sessionId: string | null } {
  const locked = Boolean(agentWorkspace) || areAgentConversationControlsLocked(phase);
  return { enabled: !locked, sessionId: locked ? null : sessionId };
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
  if (currentProfile.agent_id !== identity.agent_id)
    throw new Error("agent_conversation_identity_mismatch");
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

export function AgentWorkspaceWelcome({
  profile,
  creating,
  error,
  historyError,
  onRetryHistory,
  onStart,
  onOpenDetail,
}: {
  profile: AgentProfilePublicProjection;
  creating: boolean;
  error: string | null;
  historyError: string | null;
  onRetryHistory?: () => void;
  onStart: () => void;
  onOpenDetail: () => void;
}) {
  return (
    <main
      className="min-h-0 flex-1 overflow-y-auto bg-[var(--theme-workbench-canvas)] px-4 py-8 text-[var(--theme-text)] sm:px-6 sm:py-12"
      data-agent-workspace-welcome
    >
      <section className="mx-auto max-w-3xl overflow-hidden rounded-xl border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] shadow-sm">
        <div className="border-b border-[var(--theme-border)] bg-gradient-to-br from-emerald-50/80 via-transparent to-sky-50/70 px-6 py-7 dark:from-emerald-950/20 dark:to-sky-950/20 sm:px-8 sm:py-9">
          <div className="flex flex-col gap-5 sm:flex-row sm:items-start">
            <span
              aria-label={`${profile.name} 头像`}
              className="inline-flex h-16 w-16 shrink-0 items-center justify-center rounded-xl bg-emerald-100 text-emerald-700 shadow-sm dark:bg-emerald-950/60 dark:text-emerald-300"
              data-agent-avatar-ref={profile.avatar_ref}
              role="img"
            >
              <AgentConversationAvatar avatarRef={profile.avatar_ref} />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2 text-xs font-medium">
                <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-100 px-2.5 py-1 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-200">
                  <ShieldCheck size={14} aria-hidden="true" />
                  企业已发布
                </span>
                <span className="rounded-full bg-[var(--theme-bg-sidebar)] px-2.5 py-1 text-[var(--theme-text-secondary)]">
                  {AGENT_CATEGORY_LABELS[profile.category]}
                </span>
                <span className="rounded-full bg-[var(--theme-bg-sidebar)] px-2.5 py-1 text-[var(--theme-text-secondary)]">
                  版本 {profile.expected_revision}
                </span>
              </div>
              <h1 className="mt-4 text-2xl font-semibold tracking-tight sm:text-3xl">
                {profile.name}
              </h1>
              <p className="mt-3 max-w-2xl whitespace-pre-wrap text-sm leading-7 text-[var(--theme-text-secondary)] sm:text-base">
                {profile.description || "该智能体已通过企业平台发布，可在受控会话中使用。"}
              </p>
            </div>
          </div>
        </div>

        <div className="grid gap-4 px-6 py-6 sm:grid-cols-2 sm:px-8">
          <div className="rounded-lg border border-[var(--theme-border)] p-4">
            <h2 className="text-sm font-semibold">专属会话</h2>
            <p className="mt-2 text-sm leading-6 text-[var(--theme-text-secondary)]">
              对话会固定到这个发布版本，历史记录只在当前智能体工作区显示。
            </p>
          </div>
          <div className="rounded-lg border border-[var(--theme-border)] p-4">
            <h2 className="text-sm font-semibold">企业受控能力</h2>
            <p className="mt-2 text-sm leading-6 text-[var(--theme-text-secondary)]">
              模型、Skills 与工具由平台统一配置，使用者无需自行选择或调整。
            </p>
          </div>
        </div>

        <div className="flex flex-col gap-3 border-t border-[var(--theme-border)] px-6 py-5 sm:flex-row sm:items-center sm:justify-between sm:px-8">
          <button
            className="inline-flex items-center gap-1 text-sm font-medium text-[var(--theme-text-secondary)] hover:text-[var(--theme-primary)]"
            onClick={onOpenDetail}
            type="button"
          >
            查看智能体详情
            <ArrowRight size={15} aria-hidden="true" />
          </button>
          <button
            aria-label={`开始与 ${profile.name} 对话`}
            className="btn-primary inline-flex min-h-10 items-center justify-center gap-2 px-5 disabled:cursor-not-allowed disabled:opacity-60"
            data-agent-workspace-start
            disabled={creating}
            onClick={onStart}
            type="button"
          >
            <MessageCircle size={17} aria-hidden="true" />
            {creating ? "正在创建专属会话…" : "开始新对话"}
          </button>
        </div>
      </section>

      {error ? (
        <p className="mx-auto mt-4 max-w-3xl text-sm text-[var(--theme-danger)]" role="alert">
          {error}
        </p>
      ) : null}
      {historyError ? (
        <div className="mx-auto mt-4 flex max-w-3xl items-center justify-between gap-3 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-900/70 dark:bg-amber-950/30 dark:text-amber-100">
          <span>历史会话暂时无法加载，新建会话不受影响。</span>
          {onRetryHistory ? (
            <button className="font-medium underline" onClick={onRetryHistory} type="button">
              重试
            </button>
          ) : null}
        </div>
      ) : null}
    </main>
  );
}

export interface ChatAppContentProps {
  sidebarCollapsed: boolean;
  setSidebarCollapsed: (collapsed: boolean) => void;
  mobileSidebarOpen: boolean;
  setMobileSidebarOpen: (open: boolean) => void;
  agentWorkspace?: AgentProfilePublicProjection;
  agentWorkspaceStartProfile?: AgentProfilePublicProjection;
  agentWorkspaceSessionSource?: SessionSidebarSessionSource;
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
  agentWorkspaceSessionSource,
  agentWorkspaceHistoryError = null,
  onAgentWorkspaceHistoryRetry,
  onAgentWorkspaceSessionCreated,
}: ChatAppContentProps) {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const { sessionId: routeSessionId } = useParams<{ sessionId?: string }>();
  const [agentConversationState, setAgentConversationState] = useState(() =>
    conversationState(
      agentWorkspace && routeSessionId ? "loading" : "generic",
      routeSessionId ?? null,
    ),
  );
  const [agentWorkspaceCreating, setAgentWorkspaceCreating] = useState(false);
  const [agentWorkspaceError, setAgentWorkspaceError] = useState<string | null>(null);
  const agentWorkspaceRouteBasePath = agentWorkspace
    ? `/agent-market/${encodeURIComponent(agentWorkspace.agent_id)}/${agentWorkspace.expected_revision}/chat`
    : "/chat";
  const agentWorkspaceDetailPath = agentWorkspace
    ? `/agent-market/${encodeURIComponent(agentWorkspace.agent_id)}/${agentWorkspace.expected_revision}`
    : "/agent-market";
  const chatToolAccess = getChatToolAccess({
    agentWorkspace,
    phase: agentConversationState.phase,
    sessionId: null,
  });
  const agentConversationControlsLocked = !chatToolAccess.enabled;
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

  const sessionToolAccess = getChatToolAccess({
    agentWorkspace,
    phase: agentConversationState.phase,
    sessionId,
  });
  const agentConversationTargetSessionId = agentWorkspace
    ? routeSessionId ?? null
    : routeSessionId ?? sessionId;
  const conversationIdentityKey = agentWorkspace
    ? `${agentWorkspace.agent_id}:${agentWorkspace.expected_revision}:${routeSessionId ?? ""}`
    : `generic:${routeSessionId ?? ""}`;
  const previousConversationIdentityKeyRef = useRef<string | undefined>(
    undefined,
  );
  const agentWorkspaceSelectionRequestIdRef = useRef(0);

  // Clear before paint whenever the rendered workspace/session identity changes.
  useLayoutEffect(() => {
    if (previousConversationIdentityKeyRef.current === conversationIdentityKey) {
      return;
    }
    previousConversationIdentityKeyRef.current = conversationIdentityKey;
    agentWorkspaceSelectionRequestIdRef.current += 1;
    clearMessages();
    // A task Skill is scoped to the composer that selected it.  A route or
    // workspace identity change clears the session, so it must also clear the
    // local selector before a later submit can create an unbound conversation.
    clearSelectedSkill();
    setAgentConversationState(
      agentWorkspace && routeSessionId
        ? conversationState("loading", routeSessionId)
        : conversationState("generic", null),
    );
  }, [
    agentWorkspace,
    clearMessages,
    clearSelectedSkill,
    conversationIdentityKey,
    routeSessionId,
  ]);

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
        if (
          agentWorkspace &&
          (!identity ||
            identity.agent_id !== agentWorkspace.agent_id ||
            identity.revision !== agentWorkspace.expected_revision)
        ) {
          throw new Error("agent_workspace_revision_mismatch");
        }
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
        navigate(agentWorkspaceDetailPath, { replace: true });
      });
    return () => {
      active = false;
    };
  }, [
    agentConversationTargetSessionId,
    agentWorkspace,
    agentWorkspaceDetailPath,
    navigate,
  ]);

  const {
    tools,
    serverSelectedToolIds,
    isLoading: toolsLoading,
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
    agentConversationState.phase !== "loading" &&
    agentConversationState.phase !== "blocked" &&
    recoveredSessionReady &&
    (!agentWorkspace || agentWorkspaceTranscriptReady);

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
    sessionRouteBasePath: agentWorkspaceRouteBasePath,
    historyLoadEnabled: agentWorkspaceHistoryLoadEnabled,
  });

  const handleNewSessionWithReset = useCallback(() => {
    if (agentWorkspace) {
      if (agentWorkspaceCreating) return;
      const startProfile = agentWorkspaceStartProfile ?? agentWorkspace;
      if (
        startProfile.agent_id !== agentWorkspace.agent_id ||
        startProfile.expected_revision !== agentWorkspace.expected_revision
      ) {
        clearMessages();
        navigate(
          `/agent-market/${encodeURIComponent(startProfile.agent_id)}/${startProfile.expected_revision}/chat`,
        );
        return;
      }
      setAgentWorkspaceCreating(true);
      setAgentWorkspaceError(null);
      void agentProfileApi
        .createConversation({
          agent_id: startProfile.agent_id,
          expected_revision: startProfile.expected_revision,
        })
        .then((session) => {
          const identity = session.agent_conversation;
          if (
            !session.session_id ||
            session.agent_id !== agentWorkspace.agent_id ||
            !identity ||
            identity.agent_id !== agentWorkspace.agent_id ||
            identity.revision !== agentWorkspace.expected_revision
          ) {
            throw new Error("agent_workspace_identity_mismatch");
          }
          clearMessages();
          onAgentWorkspaceSessionCreated?.(session.session_id);
          navigate(
            `${agentWorkspaceRouteBasePath}/${encodeURIComponent(session.session_id)}`,
          );
        })
        .catch((error: unknown) => {
          const status =
            error !== null && typeof error === "object"
              ? (error as { status?: number }).status
              : undefined;
          setAgentWorkspaceError(
            status === 403
              ? "当前账号无权使用该智能体。"
              : status === 404 || status === 409
                ? "该智能体已不可用或发布版本已更新，请返回市场重新选择。"
                : "暂时无法创建智能体对话，请稍后重试。",
          );
        })
        .finally(() => setAgentWorkspaceCreating(false));
      return;
    }
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
    agentWorkspace,
    agentWorkspaceStartProfile,
    agentWorkspaceCreating,
    agentWorkspaceRouteBasePath,
    onAgentWorkspaceSessionCreated,
    clearMessages,
    navigate,
  ]);

  const handleMobileClose = useCallback(
    () => setMobileSidebarOpen(false),
    [setMobileSidebarOpen],
  );
  const handleSelectSessionAndClose = useCallback(
    async (id: string) => {
      const selectionRequestId = ++agentWorkspaceSelectionRequestIdRef.current;
      setAgentConversationState(conversationState("loading", id));
      clearMessages();
      clearSelectedSkill();
      if (agentWorkspace) {
        try {
          const identity = await recoverAgentConversationIdentity(id);
          if (selectionRequestId !== agentWorkspaceSelectionRequestIdRef.current) {
            return;
          }
          if (
            !identity ||
            identity.agent_id !== agentWorkspace.agent_id ||
            identity.revision !== agentWorkspace.expected_revision
          ) {
            throw new Error("agent_workspace_revision_mismatch");
          }
          setAgentConversationState(conversationState("bound", id, identity));
          navigate(
            `${agentWorkspaceRouteBasePath}/${encodeURIComponent(id)}`,
          );
          setMobileSidebarOpen(false);
          return;
        } catch {
          if (selectionRequestId !== agentWorkspaceSelectionRequestIdRef.current) {
            return;
          }
          setAgentConversationState(conversationState("blocked", id));
          navigate(agentWorkspaceDetailPath, { replace: true });
          toast.error("该历史对话不属于当前发布版本，请从左侧选择其他对话。");
          return;
        }
      }
      await handleSelectSession(id);
      setMobileSidebarOpen(false);
    },
    [
      agentWorkspace,
      agentWorkspaceDetailPath,
      agentWorkspaceRouteBasePath,
      clearMessages,
      clearSelectedSkill,
      handleSelectSession,
      navigate,
      setMobileSidebarOpen,
    ],
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
      currentRunId={visibleCurrentRunId}
      onOpenRunPlayback={handleOpenRunPlayback}
      showOutlineButton={shouldShowMessageOutline(visibleMessages)}
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
          sessionFilter={
            agentWorkspace && !agentWorkspaceSessionSource
              ? () => false
              : undefined
          }
          sessionSource={agentWorkspaceSessionSource}
          agentWorkspace={
            agentWorkspace
              ? {
                  name: agentWorkspace.name,
                  description: agentWorkspace.description,
                }
              : undefined
          }
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
        {agentWorkspace && !routeSessionId && !sessionId ? (
          <AgentWorkspaceWelcome
            creating={agentWorkspaceCreating}
            error={agentWorkspaceError}
            historyError={agentWorkspaceHistoryError}
            onOpenDetail={() => navigate(agentWorkspaceDetailPath)}
            onRetryHistory={onAgentWorkspaceHistoryRetry}
            onStart={handleNewSessionWithReset}
            profile={agentWorkspace}
          />
        ) : (
          <ChatMcpCatalogContext.Provider value={mcpCatalogContextValue}>
            <ChatView
            messages={visibleMessages}
            sessionId={visibleSessionId}
            currentRunId={visibleCurrentRunId}
            isLoading={isLoading}
            isLoadingHistory={isLoadingHistory}
            connectionStatus={connectionStatus}
            canSendMessage={canSendMessage}
            composerPlaceholder={
              agentWorkspace
                ? `向 ${agentWorkspace.name} 描述要完成的任务…`
                : undefined
            }
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
            sessionRouteBasePath={agentWorkspaceRouteBasePath}
            />
          </ChatMcpCatalogContext.Provider>
        )}
        <BlockPreviewPortal />
      </>
    </AppShell>
  );
}
