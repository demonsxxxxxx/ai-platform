import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { ChatAppContent } from "../../components/layout/AppContent/ChatAppContent";
import { SIDEBAR_COLLAPSED_STORAGE_KEY } from "../../hooks/useAuth";
import { authApi } from "../../services/api";
import { agentProfileApi } from "../../services/api/agentProfile";
import type { AgentProfilePublicProjection } from "../../types";
import { selectPublishedMarketProfile } from "./agentMarketSelection";

type WorkspacePhase = "loading" | "ready" | "unavailable" | "error";

/** Recover one published revision before exposing the dedicated Agent workspace. */
export function AgentWorkspaceRoute() {
  const navigate = useNavigate();
  const { agentId, revision } = useParams<{
    agentId?: string;
    revision?: string;
  }>();
  const [phase, setPhase] = useState<WorkspacePhase>("loading");
  const [profile, setProfile] = useState<AgentProfilePublicProjection | null>(
    null,
  );
  const [sessionIds, setSessionIds] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    const saved = localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY);
    return saved !== null ? saved === "true" : false;
  });

  useEffect(() => {
    let active = true;
    setPhase("loading");
    setProfile(null);
    setSessionIds(new Set());

    if (!agentId || !revision) {
      setPhase("unavailable");
      return () => {
        active = false;
      };
    }

    void Promise.all([
      agentProfileApi.getPublished(agentId),
      agentProfileApi.listConversations(),
    ])
      .then(([currentProfile, conversations]) => {
        if (!active) return;
        const exactProfile = selectPublishedMarketProfile(
          [currentProfile],
          agentId,
          revision,
        );
        if (!exactProfile) {
          setPhase("unavailable");
          return;
        }
        setProfile(exactProfile);
        setSessionIds(
          new Set(
            conversations
              .filter(
                (conversation) =>
                  conversation.agent_conversation?.agent_id ===
                    exactProfile.agent_id &&
                  conversation.agent_conversation.revision ===
                    exactProfile.expected_revision,
              )
              .map((conversation) => conversation.session_id),
          ),
        );
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
  }, [agentId, revision]);

  useEffect(() => {
    if (phase === "unavailable") {
      navigate("/agent-market", { replace: true });
    }
  }, [navigate, phase]);

  const handleSetSidebarCollapsed = (collapsed: boolean) => {
    setSidebarCollapsed(collapsed);
    localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, String(collapsed));
    authApi.updateMetadata({ sidebarCollapsed: String(collapsed) }).catch(() => {});
  };

  if (phase === "ready" && profile) {
    return (
      <ChatAppContent
        agentWorkspace={profile}
        mobileSidebarOpen={mobileSidebarOpen}
        setMobileSidebarOpen={setMobileSidebarOpen}
        setSidebarCollapsed={handleSetSidebarCollapsed}
        sidebarCollapsed={sidebarCollapsed}
        agentWorkspaceSessionIds={sessionIds}
        onAgentWorkspaceSessionCreated={(sessionId) =>
          setSessionIds((current) => new Set(current).add(sessionId))
        }
      />
    );
  }

  return (
    <main
      aria-live="polite"
      className="flex min-h-screen items-center justify-center bg-[var(--theme-workbench-canvas)] px-4 text-sm text-[var(--theme-text-secondary)]"
      data-agent-workspace-loading
    >
      {phase === "error"
        ? "暂时无法校验智能体工作区，请稍后返回市场重试。"
        : "正在校验当前发布版本…"}
    </main>
  );
}
