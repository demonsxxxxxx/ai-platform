import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { ChatAppContent } from "../../components/layout/AppContent/ChatAppContent";
import { AppShell } from "../../components/layout/AppContent/AppShell";
import { SessionSidebar } from "../../components/panels/SessionSidebar";
import type { SessionSidebarSessionSource } from "../../components/panels/SessionSidebar";
import { SIDEBAR_COLLAPSED_STORAGE_KEY } from "../../hooks/useAuth";
import { authApi } from "../../services/api";
import { agentProfileApi } from "../../services/api/agentProfile";
import { sessionApi } from "../../services/api/session";
import type {
  AgentConversationIdentity,
  AgentProfilePublicProjection,
} from "../../types/agentProfile";
import { selectPublishedMarketProfile } from "./agentMarketSelection";
import { useAgentConversationList } from "./useAgentConversationList";

type WorkspacePhase = "loading" | "ready" | "unavailable" | "error";

const EMPTY_WORKSPACE_SESSION_SOURCE: SessionSidebarSessionSource = {
  sessions: [],
  isLoading: false,
  isLoadingMore: false,
  hasMore: false,
  loadMore: async () => {},
  softRefresh: async () => {},
  prependSession: () => {},
  removeSession: () => {},
  updateSession: () => {},
};

interface LoadedAgentWorkspace {
  agentId: string;
  revision: string;
  profile: AgentProfilePublicProjection;
  startProfile: AgentProfilePublicProjection | null;
  readOnly: boolean;
}

function historicalProfile(
  identity: AgentConversationIdentity,
): AgentProfilePublicProjection {
  return {
    agent_id: identity.agent_id,
    expected_revision: identity.revision,
    name: identity.name,
    description: identity.description,
    welcome_message: identity.welcome_message,
    starter_prompts: identity.starter_prompts,
    capability_summary: identity.capability_summary,
    recommended_tasks: identity.recommended_tasks,
    supported_input_types: identity.supported_input_types,
    supported_file_types: identity.supported_file_types,
    expected_outputs: identity.expected_outputs,
    permissions_and_data_access_notice:
      identity.permissions_and_data_access_notice,
    avatar_ref: identity.avatar_ref,
    category: identity.category,
    published_at: identity.published_at,
  };
}

/** Recover one current or historical Agent revision before exposing canonical Chat. */
export function AgentWorkspaceRoute() {
  const navigate = useNavigate();
  const { agentId, revision, sessionId: routeSessionId } = useParams<{
    agentId?: string;
    revision?: string;
    sessionId?: string;
  }>();
  const parsedRevision = Number(revision);
  const validRevision =
    Number.isSafeInteger(parsedRevision) && parsedRevision > 0
      ? parsedRevision
      : undefined;
  const [phase, setPhase] = useState<WorkspacePhase>("loading");
  const [profileRetry, setProfileRetry] = useState(0);
  const [loadedWorkspace, setLoadedWorkspace] =
    useState<LoadedAgentWorkspace | null>(null);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    const saved = localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY);
    return saved !== null ? saved === "true" : false;
  });
  const historyScopeAuthorized =
    phase === "ready" &&
    loadedWorkspace !== null &&
    loadedWorkspace.agentId === agentId &&
    loadedWorkspace.revision === revision;
  const conversationList = useAgentConversationList(
    historyScopeAuthorized ? agentId : undefined,
    historyScopeAuthorized ? validRevision : undefined,
  );

  useEffect(() => {
    let active = true;
    setPhase("loading");
    setLoadedWorkspace((current) =>
      current !== null &&
      current.agentId === agentId &&
      current.revision === revision
        ? current
        : null,
    );

    if (!agentId || !revision || validRevision === undefined) {
      setPhase("unavailable");
      return () => {
        active = false;
      };
    }

    const currentProfileRequest = routeSessionId
      ? agentProfileApi.getPublished(agentId).catch(() => null)
      : agentProfileApi.getPublished(agentId);
    const sessionRequest = routeSessionId
      ? sessionApi.getAuthoritative(routeSessionId)
      : Promise.resolve(null);
    void Promise.all([currentProfileRequest, sessionRequest])
      .then(([currentProfile, session]) => {
        if (!active) return;
        if (currentProfile !== null && currentProfile.agent_id !== agentId) {
          setPhase("unavailable");
          return;
        }

        if (session !== null) {
          const identity = session.agent_conversation;
          if (
            session.session_id !== routeSessionId ||
            session.agent_id !== agentId ||
            !identity ||
            identity.agent_id !== agentId ||
            identity.revision !== validRevision
          ) {
            setPhase("unavailable");
            return;
          }
          setLoadedWorkspace({
            agentId,
            revision,
            profile: historicalProfile(identity),
            startProfile: currentProfile,
            readOnly:
              currentProfile === null ||
              currentProfile.expected_revision !== validRevision,
          });
          setPhase("ready");
          return;
        }

        const exactProfile = selectPublishedMarketProfile(
          currentProfile ? [currentProfile] : [],
          agentId,
          revision,
        );
        if (!exactProfile) {
          setPhase("unavailable");
          return;
        }
        setLoadedWorkspace({
          agentId,
          revision,
          profile: exactProfile,
          startProfile: currentProfile,
          readOnly: false,
        });
        setPhase("ready");
      })
      .catch((error: unknown) => {
        if (!active) return;
        const status =
          error !== null && typeof error === "object"
            ? (error as { status?: number }).status
            : undefined;
        setPhase(status === 403 || status === 404 ? "unavailable" : "error");
      });

    return () => {
      active = false;
    };
  }, [agentId, profileRetry, revision, routeSessionId, validRevision]);

  useEffect(() => {
    if (phase === "unavailable") {
      navigate("/agent-market", { replace: true });
    }
  }, [navigate, phase]);

  const handleSetSidebarCollapsed = useCallback((collapsed: boolean) => {
    setSidebarCollapsed(collapsed);
    localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, String(collapsed));
    authApi.updateMetadata({ sidebarCollapsed: String(collapsed) }).catch(() => {});
  }, []);

  // Route params can change before the passive fetch cleanup runs. Never let
  // the previous URL's profile reach canonical Chat.
  const resolvedWorkspace =
    phase === "ready" &&
    loadedWorkspace !== null &&
    loadedWorkspace.agentId === agentId &&
    loadedWorkspace.revision === revision
      ? loadedWorkspace
      : null;

  if (resolvedWorkspace) {
    return (
      <ChatAppContent
        agentWorkspace={resolvedWorkspace.profile}
        agentWorkspaceHistoryError={conversationList.error}
        agentWorkspaceReadOnly={resolvedWorkspace.readOnly}
        agentWorkspaceSessionSource={conversationList}
        agentWorkspaceStartProfile={resolvedWorkspace.startProfile ?? undefined}
        mobileSidebarOpen={mobileSidebarOpen}
        onAgentWorkspaceHistoryRetry={conversationList.refresh}
        onAgentWorkspaceSessionCreated={() => void conversationList.refresh()}
        setMobileSidebarOpen={setMobileSidebarOpen}
        setSidebarCollapsed={handleSetSidebarCollapsed}
        sidebarCollapsed={sidebarCollapsed}
      />
    );
  }

  const handleGenericSession = (sessionId: string) => {
    setMobileSidebarOpen(false);
    navigate(`/chat/${encodeURIComponent(sessionId)}`);
  };
  const handleGenericNewSession = () => {
    setMobileSidebarOpen(false);
    navigate("/chat");
  };

  return (
    <AppShell
      activeTab="chat"
      onNewSession={handleGenericNewSession}
      setMobileSidebarOpen={setMobileSidebarOpen}
      sidebar={
        <SessionSidebar
          currentSessionId={null}
          isCollapsed={sidebarCollapsed}
          mobileOpen={mobileSidebarOpen}
          onMobileClose={() => setMobileSidebarOpen(false)}
          onMobileOpen={() => setMobileSidebarOpen(true)}
          onNewSession={handleGenericNewSession}
          onSelectSession={handleGenericSession}
          onToggleCollapsed={handleSetSidebarCollapsed}
          sessionSource={EMPTY_WORKSPACE_SESSION_SOURCE}
        />
      }
    >
      <main
        aria-live="polite"
        className="flex min-h-0 flex-1 items-center justify-center bg-[var(--theme-workbench-canvas)] px-4 text-sm text-[var(--theme-text-secondary)] sm:px-6"
        data-agent-workspace-loading
      >
        <div className="max-w-md text-center">
          <p>
            {phase === "error"
              ? "暂时无法校验智能体工作区。"
              : "正在校验当前智能体与会话权限…"}
          </p>
          {phase === "error" ? (
            <div className="mt-4 flex justify-center gap-3">
              <button
                className="btn-primary"
                onClick={() => setProfileRetry((current) => current + 1)}
                type="button"
              >
                重新加载
              </button>
              <button
                className="btn-secondary"
                onClick={() => navigate("/agent-market")}
                type="button"
              >
                返回市场
              </button>
            </div>
          ) : null}
        </div>
      </main>
    </AppShell>
  );
}
