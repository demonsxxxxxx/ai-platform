/* eslint-disable react-refresh/only-export-components -- behavioral seams stay with the canonical Chat owner */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import toast from "react-hot-toast";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { History } from "lucide-react";
import { AgentIdentityAvatar } from "../../agent/AgentIdentityAvatar";
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
import { uuid } from "../../../utils/uuid";
import {
  AGENT_PROFILE_CATEGORY_LABELS,
  type AgentConversationIdentity,
  type AgentProfilePublicProjection,
} from "../../../types/agentProfile";
import {
  buildAgentMarketDetailPath,
  buildAgentMarketWorkspacePath,
} from "../../../features/agent-market/agentMarketSelection";

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

const AGENT_CONVERSATION_OPERATION_STORAGE_PREFIX = "agent-conversation-operation:";
const UUID_V4_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function agentConversationOperationStorageKey(agentId: string, revision: number): string {
  return `${AGENT_CONVERSATION_OPERATION_STORAGE_PREFIX}${agentId}:${revision}`;
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
  revision,
  storage,
  createId,
}: {
  agentId: string;
  revision: number;
  storage: Pick<Storage, "getItem" | "setItem"> | null;
  createId: () => string;
}): string | null {
  const key = agentConversationOperationStorageKey(agentId, revision);
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
  revision,
  storage,
}: {
  agentId: string;
  revision: number;
  storage: Pick<Storage, "removeItem"> | null;
}): void {
  storage?.removeItem(agentConversationOperationStorageKey(agentId, revision));
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
  bindConversation,
}: {
  coordinator: AgentFirstSendCoordinator;
  profile: Pick<AgentProfilePublicProjection, "agent_id" | "expected_revision">;
  createConversation: () => ReturnType<typeof agentProfileApi.createConversation>;
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
        identity.agent_id !== profile.agent_id ||
        identity.revision !== profile.expected_revision
      ) {
        throw new Error("agent_workspace_identity_mismatch");
      }
      if (!(await bindConversation(created.session_id))) {
        throw new Error("agent_conversation_history_unavailable");
      }
      return created.session_id;
    })();
  }
  try {
    return await coordinator.current;
  } catch (error) {
    coordinator.current = null;
    throw error;
  }
}

/** Share the entire first-send result so click/keyboard races submit once. */
export async function submitAgentFirstMessageSingleFlight({
  coordinator,
  submissionKey,
  ensureConversation,
  submitMessage,
}: {
  coordinator: AgentFirstSubmissionCoordinator;
  submissionKey: string;
  ensureConversation: () => Promise<string>;
  submitMessage: (sessionId: string) => Promise<SubmissionOutcome>;
}): Promise<SubmissionOutcome> {
  const active = coordinator.current;
  if (active && active.submissionKey !== submissionKey) {
    return { status: "failed" };
  }
  let flight = active?.promise;
  if (!active) {
    flight = (async () => {
      const createdSessionId = await ensureConversation();
      return submitMessage(createdSessionId);
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
  return identity;
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
        <AgentIdentityAvatar
          agentId={identity.agent_id}
          avatarRef={identity.avatar_ref}
          avatarSeed={identity.avatar_seed}
          name={identity.name}
          size="sm"
        />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <strong className="text-sm font-semibold sm:text-base">{identity.name}</strong>
            <span className="text-xs text-[var(--theme-text-secondary)]">
              {AGENT_PROFILE_CATEGORY_LABELS[identity.category]}
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
  agentWorkspace?: AgentProfilePublicProjection;
  agentWorkspaceStartProfile?: AgentProfilePublicProjection;
  agentWorkspaceReadOnly?: boolean;
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
  agentWorkspaceReadOnly = false,
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
  const agentWorkspaceCreationRef = useRef<Promise<string> | null>(null);
  const agentWorkspaceFirstSubmissionRef =
    useRef<AgentFirstSubmissionCoordinator["current"]>(null);
  const [agentWorkspaceError, setAgentWorkspaceError] = useState<string | null>(null);
  const agentWorkspaceRouteBasePath = agentWorkspace
    ? buildAgentMarketWorkspacePath(agentWorkspace)
    : "/chat";
  const agentWorkspaceDetailPath = agentWorkspace
    ? buildAgentMarketDetailPath(agentWorkspace)
    : "/agent-market";
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
  const agentWorkspaceSelectionRequestIdRef = useRef(0);

  // Clear before paint whenever the rendered workspace/session identity changes.
  useConversationRouteIdentityReset({
    conversationIdentityKey,
    hasAgentWorkspace: Boolean(agentWorkspace),
    routeSessionId,
    sessionId,
    onIdentityChange: () => {
      agentWorkspaceSelectionRequestIdRef.current += 1;
      setAgentWorkspaceError(null);
      clearMessages();
      // A task Skill is scoped to the composer that selected it. A route or
      // workspace identity change clears the session, so it must also clear the
      // local selector before a later submit can create an unbound conversation.
      clearSelectedSkill();
      setAgentConversationState(
        agentWorkspace && routeSessionId
          ? conversationState("loading", routeSessionId)
          : conversationState("generic", null),
      );
    },
  });

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
        if (!agentWorkspace && identity) {
          navigate(
            buildAgentMarketWorkspacePath(
              {
                agent_id: identity.agent_id,
                expected_revision: identity.revision,
              },
              agentConversationTargetSessionId,
            ),
            { replace: true },
          );
        }
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
    !agentWorkspaceReadOnly &&
    agentConversationState.phase !== "loading" &&
    agentConversationState.phase !== "blocked" &&
    recoveredSessionReady &&
    (!agentWorkspace ||
      (!routeSessionId && !sessionId) ||
      agentWorkspaceTranscriptReady);

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
      agentWorkspaceCreationRef.current = null;
      agentWorkspaceFirstSubmissionRef.current = null;
      setAgentWorkspaceError(null);
      clearMessages();
      setAgentConversationState(conversationState("generic", null));
      navigate(agentWorkspaceRouteBasePath);
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
    agentWorkspaceRouteBasePath,
    clearMessages,
    navigate,
  ]);

  const handleSendMessage = useCallback(
    async (
      content: string,
      options?: Record<string, boolean | string | number>,
      attachments?: MessageAttachment[],
      selectedSkill?: SelectedSkillRequest | null,
    ): Promise<SubmissionOutcome> => {
      setAgentWorkspaceError(null);
      if (!agentWorkspace || sessionId) {
        return sendMessage(content, options, attachments, selectedSkill);
      }
      const startProfile = agentWorkspaceStartProfile;
      if (agentWorkspaceReadOnly || !startProfile) {
        setAgentWorkspaceError("该专家已下架，历史会话仅供查看。");
        return { status: "failed" };
      }
      if (
        startProfile.agent_id !== agentWorkspace.agent_id ||
        startProfile.expected_revision !== agentWorkspace.expected_revision
      ) {
        navigate(
          buildAgentMarketWorkspacePath(startProfile),
          { replace: true },
        );
        return { status: "failed" };
      }

      try {
        const outcome = await submitAgentFirstMessageSingleFlight({
          coordinator: agentWorkspaceFirstSubmissionRef,
          submissionKey: JSON.stringify({
            content,
            fileIds: (attachments ?? []).map((attachment) => attachment.key),
          }),
          ensureConversation: () =>
            ensureAgentConversationForFirstSend({
              coordinator: agentWorkspaceCreationRef,
              profile: startProfile,
              createConversation: () => {
                const operationId = getOrCreateAgentConversationOperationId({
                  agentId: startProfile.agent_id,
                  revision: startProfile.expected_revision,
                  storage: browserSessionStorage(),
                  createId: uuid,
                });
                if (!operationId) {
                  return Promise.reject(
                    new Error("agent_conversation_operation_storage_unavailable"),
                  );
                }
                return agentProfileApi.createConversation(
                  {
                    agent_id: startProfile.agent_id,
                    expected_revision: startProfile.expected_revision,
                  },
                  operationId,
                );
              },
              bindConversation: async (createdSessionId) =>
                Boolean(await loadHistory(createdSessionId)),
            }),
          submitMessage: async (createdSessionId) => {
            setAgentWorkspaceError(null);
            const submission = sendMessage(content, undefined, attachments, null);
            navigate(
              buildAgentMarketWorkspacePath(startProfile, createdSessionId),
              { replace: true },
            );
            onAgentWorkspaceSessionCreated?.(createdSessionId);
            return submission;
          },
        });
        if (outcome.status === "accepted") {
          clearAgentConversationOperationId({
            agentId: startProfile.agent_id,
            revision: startProfile.expected_revision,
            storage: browserSessionStorage(),
          });
        }
        return outcome;
      } catch (error) {
        agentWorkspaceFirstSubmissionRef.current = null;
        const status =
          error !== null && typeof error === "object"
            ? (error as { status?: number }).status
            : undefined;
        setAgentWorkspaceError(
          error instanceof Error &&
            error.message === "agent_conversation_operation_storage_unavailable"
            ? "浏览器无法安全保存本次创建标识，请启用会话存储后重试。"
            : status === 403
              ? "当前账号无权使用该专家。"
              : status === 404 || status === 409
                ? "该专家已不可用或发布版本已更新，请返回市场重新选择。"
                : "暂时无法创建专家对话，请稍后重试。",
        );
        return { status: "failed" };
      }
    },
    [
      agentWorkspace,
      agentWorkspaceReadOnly,
      agentWorkspaceStartProfile,
      loadHistory,
      navigate,
      onAgentWorkspaceSessionCreated,
      sendMessage,
      sessionId,
    ],
  );

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
      allowNewSessionAction={agentWorkspace !== undefined}
      newSessionActionLabel={agentWorkspace ? "开始新任务" : undefined}
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
          navigationOnly={agentWorkspace === undefined}
        />
      }
    >
      <div className="min-h-0 flex-1 overflow-hidden flex flex-col">
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
            messages={visibleMessages}
            sessionId={visibleSessionId}
            currentRunId={visibleCurrentRunId}
            isLoading={isLoading}
            isLoadingHistory={isLoadingHistory}
            connectionStatus={connectionStatus}
            canSendMessage={canSendMessage}
            initialComposerDraft={agentWorkspaceStarterDraft}
            initialComposerDraftKey={location.key}
            agentEmptyProfile={agentWorkspace}
            composerPlaceholder={
              agentWorkspaceReadOnly
                ? "该历史会话为只读状态"
                : agentWorkspace
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
            onSendMessage={handleSendMessage}
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
        {agentWorkspaceError ? (
          <p className="px-4 pb-2 text-center text-sm text-[var(--theme-danger)]" role="alert">
            {agentWorkspaceError}
          </p>
        ) : null}
        {agentWorkspaceHistoryError ? (
          <div className="flex items-center justify-center gap-3 px-4 pb-2 text-sm text-[var(--theme-warning)]">
            <span>历史会话暂时无法加载。</span>
            {onAgentWorkspaceHistoryRetry ? (
              <button className="underline" onClick={onAgentWorkspaceHistoryRetry} type="button">
                重试
              </button>
            ) : null}
          </div>
        ) : null}
        <BlockPreviewPortal />
      </div>
    </AppShell>
  );
}
