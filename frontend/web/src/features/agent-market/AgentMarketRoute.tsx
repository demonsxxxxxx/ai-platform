import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Bot, RefreshCw } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";

import { AppShell } from "../../components/layout/AppContent/AppShell";
import { SessionSidebar } from "../../components/panels/SessionSidebar";
import { SIDEBAR_COLLAPSED_STORAGE_KEY } from "../../hooks/useAuth";
import { authApi } from "../../services/api";
import { agentProfileApi } from "../../services/api/agentProfile";
import type { AgentProfilePublicProjection } from "../../types";
import { APP_ROUTE_PATHS } from "../../appRouteManifest";
import { marketProfileRequest, setPendingAgentMarketSelection } from "./agentMarketSelection";

type CatalogState =
  | { phase: "loading"; profiles: readonly AgentProfilePublicProjection[]; error: null }
  | { phase: "ready"; profiles: readonly AgentProfilePublicProjection[]; error: null }
  | { phase: "error"; profiles: readonly AgentProfilePublicProjection[]; error: string };

const MARKET_CATALOG_LOAD_ERROR = "暂时无法加载已发布的智能体，请稍后重新加载。";
const CANONICAL_CHAT_PATH = APP_ROUTE_PATHS.chat.replace("/:sessionId?", "");

interface AgentMarketShellProps {
  children: ReactNode;
}

/** Reuse the production shell and session sidebar for the ordinary-user market. */
function AgentMarketShell({ children }: AgentMarketShellProps) {
  const navigate = useNavigate();
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    const saved = localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY);
    return saved !== null ? saved === "true" : false;
  });

  const handleSetSidebarCollapsed = useCallback((collapsed: boolean) => {
    setSidebarCollapsed(collapsed);
    localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, String(collapsed));
    authApi.updateMetadata({ sidebarCollapsed: String(collapsed) }).catch(() => {});
  }, []);
  const handleSelectSession = useCallback(
    (sessionId: string) => {
      setMobileSidebarOpen(false);
      navigate(`/chat/${encodeURIComponent(sessionId)}`);
    },
    [navigate],
  );
  const handleNewSession = useCallback(() => {
    setMobileSidebarOpen(false);
    navigate(CANONICAL_CHAT_PATH);
  }, [navigate]);

  return (
    <AppShell
      activeTab="chat"
      setMobileSidebarOpen={setMobileSidebarOpen}
      onNewSession={handleNewSession}
      sidebar={
        <SessionSidebar
          currentSessionId={null}
          onSelectSession={handleSelectSession}
          onNewSession={handleNewSession}
          mobileOpen={mobileSidebarOpen}
          onMobileOpen={() => setMobileSidebarOpen(true)}
          onMobileClose={() => setMobileSidebarOpen(false)}
          isCollapsed={sidebarCollapsed}
          onToggleCollapsed={handleSetSidebarCollapsed}
        />
      }
    >
      {children}
    </AppShell>
  );
}

/** Ordinary-user published-profile catalog; Chat remains the canonical route. */
export function AgentMarketRoute() {
  const navigate = useNavigate();
  const { agentId, revision } = useParams<{ agentId?: string; revision?: string }>();
  const isLegacyMarketChatRoute = Boolean(agentId || revision);
  const [catalog, setCatalog] = useState<CatalogState>({
    phase: "loading",
    profiles: [],
    error: null,
  });
  const [requestRevision, setRequestRevision] = useState(0);

  useEffect(() => {
    if (isLegacyMarketChatRoute) {
      setCatalog({ phase: "ready", profiles: [], error: null });
      return;
    }
    let active = true;
    setCatalog((current) => ({ ...current, phase: "loading", error: null }));
    void agentProfileApi
      .listPublished()
      .then((response) => {
        if (active) {
          setCatalog({ phase: "ready", profiles: response.agent_profiles, error: null });
        }
      })
      .catch(() => {
        if (active) {
          setCatalog({
            phase: "error",
            profiles: [],
            error: MARKET_CATALOG_LOAD_ERROR,
          });
        }
      });
    return () => {
      active = false;
    };
  }, [isLegacyMarketChatRoute, requestRevision]);

  const refresh = useCallback(() => {
    setRequestRevision((current) => current + 1);
  }, []);

  const handleSelectProfile = useCallback(
    (profile: AgentProfilePublicProjection) => {
      setPendingAgentMarketSelection(marketProfileRequest(profile));
      navigate(CANONICAL_CHAT_PATH);
    },
    [navigate],
  );

  if (isLegacyMarketChatRoute) {
    return (
      <AgentMarketShell>
        <main
          data-agent-market-invalid
          className="min-h-0 flex-1 overflow-y-auto text-[var(--theme-text)]"
        >
          <section className="mx-auto flex w-full max-w-2xl flex-col gap-4 px-4 py-10 sm:px-6">
            <h1 className="text-xl font-semibold">该智能体入口已失效</h1>
            <p className="text-sm text-[var(--theme-text-secondary)]">
              为保护已发布版本绑定，不能从过期或未知链接启动对话。请返回市场重新选择已发布的智能体。
            </p>
            <button
              className="btn-secondary w-fit"
              onClick={() => navigate(APP_ROUTE_PATHS.agentMarket, { replace: true })}
              type="button"
            >
              返回智能体市场
            </button>
          </section>
        </main>
      </AgentMarketShell>
    );
  }

  return (
    <AgentMarketShell>
      <main
        data-agent-market
        className="min-h-0 flex-1 overflow-y-auto text-[var(--theme-text)]"
      >
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-8 sm:px-6">
          <header className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="text-sm font-medium text-[var(--theme-primary)]">智能体市场</p>
              <h1 className="mt-1 text-2xl font-semibold">选择已发布的智能体</h1>
              <p className="mt-2 max-w-2xl text-sm text-[var(--theme-text-secondary)]">
                仅展示当前租户中已发布且可用的智能体。配置与能力版本由平台在开始对话时校验。
              </p>
            </div>
            <button
              className="btn-secondary inline-flex items-center gap-2"
              onClick={refresh}
              type="button"
            >
              <RefreshCw
                size={16}
                className={catalog.phase === "loading" ? "animate-spin" : undefined}
                aria-hidden="true"
              />
              刷新
            </button>
          </header>

          {catalog.phase === "error" ? (
            <section className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-800 dark:border-red-900/70 dark:bg-red-950/40 dark:text-red-200">
              <p>{catalog.error}</p>
              <button className="btn-secondary mt-3" onClick={refresh} type="button">
                重新加载
              </button>
            </section>
          ) : catalog.phase === "loading" ? (
            <p className="text-sm text-[var(--theme-text-secondary)]">正在加载已发布的智能体…</p>
          ) : catalog.profiles.length === 0 ? (
            <section className="rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-6 text-sm text-[var(--theme-text-secondary)]">
              当前没有可用的已发布智能体，请联系管理员发布后再试。
            </section>
          ) : (
            <section
              aria-label="已发布智能体"
              className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
            >
              {catalog.profiles.map((profile) => (
                <button
                  key={`${profile.agent_id}:${profile.expected_revision}`}
                  className="flex min-h-44 flex-col rounded-xl border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-5 text-left transition-colors hover:border-[var(--theme-primary)] focus:outline-none focus:ring-2 focus:ring-[var(--theme-primary)]"
                  onClick={() => handleSelectProfile(profile)}
                  type="button"
                >
                  <Bot size={22} className="text-[var(--theme-primary)]" aria-hidden="true" />
                  <h2 className="mt-4 text-base font-semibold">{profile.name}</h2>
                  <p className="mt-2 line-clamp-3 text-sm text-[var(--theme-text-secondary)]">
                    {profile.description || "该智能体已通过平台发布。"}
                  </p>
                  <span className="mt-auto pt-4 text-sm font-medium text-[var(--theme-primary)]">
                    开始对话
                  </span>
                </button>
              ))}
            </section>
          )}
        </div>
      </main>
    </AgentMarketShell>
  );
}
