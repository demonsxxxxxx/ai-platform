import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  ArrowLeft,
  ArrowRight,
  BadgeCheck,
  MessageCircle,
  RefreshCw,
  Search,
  ShieldCheck,
} from "lucide-react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { APP_ROUTE_PATHS } from "../../appRouteManifest";
import { AppShell } from "../../components/layout/AppContent/AppShell";
import { SessionSidebar } from "../../components/panels/SessionSidebar";
import { SIDEBAR_COLLAPSED_STORAGE_KEY } from "../../hooks/useAuth";
import { authApi } from "../../services/api";
import { agentProfileApi } from "../../services/api/agentProfile";
import type { AgentProfilePublicProjection } from "../../types";
import {
  AGENT_PROFILE_CATEGORIES,
  AGENT_PROFILE_CATEGORY_LABELS,
  type AgentProfileCategory,
} from "../../types/agentProfile";
import {
  buildAgentMarketDetailPath,
  buildAgentMarketWorkspacePath,
  filterPublishedMarketProfiles,
  selectPublishedMarketProfile,
} from "./agentMarketSelection";
import { AgentIdentityAvatar } from "../../components/agent/AgentIdentityAvatar";

type LoadPhase = "loading" | "ready" | "error" | "unavailable";
interface LoadState<T> {
  key: string;
  phase: LoadPhase;
  value: T;
  error: string | null;
}
type CatalogState = LoadState<readonly AgentProfilePublicProjection[]>;
type DetailState = LoadState<AgentProfilePublicProjection | null>;

function loadState<T>(key: string, value: T, phase: LoadPhase = "loading", error: string | null = null): LoadState<T> {
  return { key, phase, value, error };
}

const MARKET_CATALOG_LOAD_ERROR = "暂时无法加载已发布的专家，请稍后重新加载。";
const MARKET_CATEGORIES: ReadonlyArray<{ value: AgentProfileCategory | "all"; label: string }> = [
  { value: "all", label: "全部" },
  ...AGENT_PROFILE_CATEGORIES.map((value) => ({
    value,
    label: AGENT_PROFILE_CATEGORY_LABELS[value],
  })),
];

/** Reuse the production shell and session sidebar for the ordinary-user market. */
function AgentMarketShell({ children }: { children: ReactNode }) {
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
    (_sessionId: string) => {
      setMobileSidebarOpen(false);
      navigate("/agent-market");
    },
    [navigate],
  );
  const handleNewSession = useCallback(() => {
    setMobileSidebarOpen(false);
    navigate("/agent-market");
  }, [navigate]);

  return (
    <AppShell
      activeTab="chat"
      setMobileSidebarOpen={setMobileSidebarOpen}
      onNewSession={handleNewSession}
      allowNewSessionAction={false}
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
          navigationOnly
        />
      }
    >
      {children}
    </AppShell>
  );
}

function usePublishedAgentCatalog(
  catalogKey: string,
  query: string | undefined,
  category: AgentProfileCategory | undefined,
  enabled: boolean,
) {
  const [retry, setRetry] = useState(0);
  const [catalog, setCatalog] = useState<CatalogState>(() => loadState(catalogKey, []));

  useEffect(() => {
    if (!enabled) return;
    let active = true;
    setCatalog(loadState(catalogKey, []));
    void agentProfileApi
      .listPublished({ query, category })
      .then((response) => {
        if (active)
          setCatalog(loadState(catalogKey, response.agent_profiles, "ready"));
      })
      .catch(() => {
        if (active)
          setCatalog(loadState(catalogKey, [], "error", MARKET_CATALOG_LOAD_ERROR));
      });
    return () => { active = false; };
  }, [catalogKey, enabled, category, query, retry]);

  const refresh = useCallback(() => setRetry((current) => current + 1), []);
  return { catalog: catalog.key === catalogKey ? catalog : loadState(catalogKey, []), refresh };
}

function getErrorStatus(error: unknown): number | undefined {
  return error !== null && typeof error === "object"
    ? (error as { status?: number }).status
    : undefined;
}

function usePublishedAgentDetail(
  detailKey: string,
  agentId: string | undefined,
  revision: string | undefined,
  enabled: boolean,
) {
  const [retry, setRetry] = useState(0);
  const [detail, setDetail] = useState<DetailState>(() => loadState(detailKey, null));

  useEffect(() => {
    if (!enabled) return;
    if (!agentId || !revision) {
      setDetail(loadState(detailKey, null, "unavailable"));
      return;
    }
    let active = true;
    setDetail(loadState(detailKey, null));
    void agentProfileApi
      .getPublished(agentId)
      .then((profile) => {
        if (!active) return;
        const exact = selectPublishedMarketProfile([profile], agentId, revision);
        setDetail(
          exact
            ? { key: detailKey, phase: "ready", value: exact, error: null }
            : loadState(detailKey, null, "unavailable"),
        );
      })
      .catch((error: unknown) => {
        if (!active) return;
        const status = getErrorStatus(error);
        setDetail(
          status === 403 || status === 404
            ? loadState(detailKey, null, "unavailable")
            : loadState(detailKey, null, "error", MARKET_CATALOG_LOAD_ERROR),
        );
      });
    return () => { active = false; };
  }, [agentId, detailKey, enabled, retry, revision]);

  const refresh = useCallback(() => setRetry((current) => current + 1), []);
  return { detail: detail.key === detailKey ? detail : loadState(detailKey, null), refresh };
}

function CatalogError({ error, refresh }: { error: string; refresh: () => void }) {
  return (
    <section
      aria-live="polite"
      className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-800 dark:border-red-900/70 dark:bg-red-950/40 dark:text-red-200"
    >
      <p>{error}</p>
      <button className="btn-secondary mt-3" onClick={refresh} type="button">
        重新加载
      </button>
    </section>
  );
}

function ExpertMarketCard({
  profile,
  onOpenWorkspace,
  onOpenDetail,
}: {
  profile: AgentProfilePublicProjection;
  onOpenWorkspace: (profile: AgentProfilePublicProjection) => void;
  onOpenDetail: (profile: AgentProfilePublicProjection) => void;
}) {
  return (
    <article
      data-agent-market-card
      className="group flex min-h-72 min-w-0 flex-col overflow-hidden rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] shadow-[0_1px_3px_rgba(15,23,42,0.05)] transition-[border-color,box-shadow] hover:border-[var(--theme-primary)] hover:shadow-[0_8px_24px_rgba(15,23,42,0.08)]"
    >
      <div className="flex flex-1 flex-col p-5">
        <div className="flex items-start gap-3">
          <AgentIdentityAvatar
            agentId={profile.agent_id}
            avatarRef={profile.avatar_ref}
            avatarSeed={profile.avatar_seed}
            name={profile.name}
            size="lg"
          />
          <div className="min-w-0 flex-1">
            <div className="flex items-start justify-between gap-2">
              <h2 className="line-clamp-2 text-base font-semibold leading-6 text-[var(--theme-text)]">
                {profile.name}
              </h2>
              <BadgeCheck
                aria-label="企业已发布"
                className="mt-0.5 shrink-0 text-[var(--theme-success)]"
                size={17}
              />
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-[var(--theme-text-secondary)]">
              <span>{AGENT_PROFILE_CATEGORY_LABELS[profile.category]}</span>
              <span aria-hidden="true">·</span>
              <span className="tabular-nums">版本 {profile.expected_revision}</span>
            </div>
          </div>
        </div>

        <p className="mt-4 line-clamp-3 text-sm leading-6 text-[var(--theme-text-secondary)]">
          {profile.capability_summary || profile.description || "已由管理员发布，可直接开始企业任务。"}
        </p>

        {profile.recommended_tasks.length > 0 ? (
          <div className="mt-4 flex flex-wrap gap-1.5" aria-label="推荐任务">
            {profile.recommended_tasks.slice(0, 3).map((task) => (
              <span
                className="max-w-full truncate rounded-md bg-[var(--theme-bg-sidebar)] px-2 py-1 text-xs text-[var(--theme-text-secondary)]"
                key={task}
                title={task}
              >
                {task}
              </span>
            ))}
          </div>
        ) : null}
      </div>
      <div className="grid grid-cols-[1fr_auto] border-t border-[var(--theme-border)]">
        <button
          data-agent-market-open-workspace
          aria-label={`使用 ${profile.name} 开始任务`}
          className="inline-flex min-h-12 items-center gap-2 px-5 text-left text-sm font-semibold text-[var(--theme-primary)] transition-colors hover:bg-[var(--theme-bg-sidebar)] focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--theme-primary)]"
          onClick={() => onOpenWorkspace(profile)}
          type="button"
        >
          开始任务
          <ArrowRight aria-hidden="true" size={15} />
        </button>
        <button
          data-agent-market-open-detail
          aria-label={`查看 ${profile.name} 详情`}
          className="min-h-12 border-l border-[var(--theme-border)] px-5 text-sm font-medium text-[var(--theme-text-secondary)] hover:bg-[var(--theme-bg-sidebar)] hover:text-[var(--theme-primary)] focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--theme-primary)]"
          onClick={() => onOpenDetail(profile)}
          type="button"
        >
          查看详情
        </button>
      </div>
    </article>
  );
}

function AgentMarketCatalog({
  catalog,
  refresh,
  activeCategory,
}: {
  catalog: CatalogState;
  refresh: () => void;
  activeCategory: AgentProfileCategory | "all";
}) {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const searchQuery = searchParams.get("q") ?? "";
  const hasActiveFilter =
    searchQuery.trim().length > 0 || activeCategory !== "all";
  const visibleProfiles = useMemo(
    () =>
      filterPublishedMarketProfiles(catalog.value, searchQuery).filter(
        (profile) =>
          activeCategory === "all" || profile.category === activeCategory,
      ),
    [activeCategory, catalog.value, searchQuery],
  );

  const handleOpenWorkspace = useCallback(
    (profile: AgentProfilePublicProjection) => {
      navigate(buildAgentMarketWorkspacePath(profile));
    },
    [navigate],
  );

  const handleCategory = useCallback(
    (category: AgentProfileCategory | "all") => {
      const next = new URLSearchParams(searchParams);
      if (category === "all") next.delete("category");
      else next.set("category", category);
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const handleSearch = useCallback(
    (query: string) => {
      const next = new URLSearchParams(searchParams);
      if (query) next.set("q", query);
      else next.delete("q");
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const handleRefresh = useCallback(() => {
    refresh();
  }, [refresh]);
  const handleClearFilters = useCallback(() => {
    setSearchParams(new URLSearchParams(), { replace: true });
  }, [setSearchParams]);

  return (
    <main data-agent-market className="min-h-0 flex-1 overflow-y-auto bg-[var(--theme-workbench-canvas)] text-[var(--theme-text)]">
      <div className="mx-auto flex w-full max-w-[86rem] flex-col px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-sm font-medium text-[var(--theme-primary)]">
              <ShieldCheck size={16} aria-hidden="true" />
              企业专家目录
            </div>
            <h1 className="mt-2 text-2xl font-semibold">专家市场</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-[var(--theme-text-secondary)]">
              选择一位企业专家，直接描述要完成的任务。能力、模型与工具由管理员统一治理。
            </p>
          </div>
          <button
            aria-label="刷新专家目录"
            className="btn-secondary inline-flex items-center gap-2"
            disabled={catalog.phase === "loading"}
            onClick={handleRefresh}
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

        <div className="sticky top-0 z-10 mt-6 border-y border-[var(--theme-border)] bg-[var(--theme-workbench-canvas)] py-4">
          <div className="grid gap-3 lg:grid-cols-[minmax(18rem,34rem)_minmax(0,1fr)] lg:items-center">
            <label className="relative block w-full">
              <span className="sr-only">搜索专家</span>
              <Search
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--theme-text-secondary)]"
                size={17}
                aria-hidden="true"
              />
              <input
                data-agent-market-search
                aria-label="搜索专家"
                className="h-11 w-full rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] pl-10 pr-3 text-sm text-[var(--theme-text)] outline-none placeholder:text-[var(--theme-text-secondary)] focus:border-[var(--theme-primary)] focus:ring-2 focus:ring-[var(--theme-primary)]/20"
                maxLength={160}
                onChange={(event) => handleSearch(event.target.value)}
                placeholder="搜索专家名称、能力或任务"
                type="search"
                value={searchQuery}
              />
            </label>
            <div
              data-agent-market-filter
              aria-label="专家分类"
              className="flex max-w-full flex-wrap items-center gap-1 lg:justify-end"
              role="group"
            >
              {MARKET_CATEGORIES.map((category) => (
                <button
                  aria-pressed={activeCategory === category.value}
                  className={`min-h-9 rounded-md border px-3 text-xs transition-colors ${
                    activeCategory === category.value
                      ? "border-[var(--theme-primary)] bg-[var(--theme-primary)] text-white"
                      : "border-transparent text-[var(--theme-text-secondary)] hover:border-[var(--theme-border)] hover:bg-[var(--theme-workbench-panel)] hover:text-[var(--theme-text)]"
                  }`}
                  key={category.value}
                  onClick={() => handleCategory(category.value)}
                  type="button"
                >
                  {category.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        {catalog.phase === "error" ? (
          <CatalogError error={catalog.error ?? MARKET_CATALOG_LOAD_ERROR} refresh={handleRefresh} />
        ) : catalog.phase === "loading" ? (
          <p aria-live="polite" className="py-8 text-sm text-[var(--theme-text-secondary)]">
            正在加载已发布的专家…
          </p>
        ) : catalog.value.length === 0 && !hasActiveFilter ? (
          <section className="border-t border-[var(--theme-border)] py-10">
            <h2 className="text-base font-semibold">当前没有可用的专家</h2>
            <p className="mt-2 text-sm text-[var(--theme-text-secondary)]">
              发布目录可能正在更新，你可以重新加载查看最新状态。
            </p>
            <button className="btn-secondary mt-4" onClick={handleRefresh} type="button">
              重新加载目录
            </button>
          </section>
        ) : visibleProfiles.length === 0 ? (
          <section aria-live="polite" className="border-t border-[var(--theme-border)] py-10">
            <h2 className="text-base font-semibold">没有匹配的专家</h2>
            <p className="mt-2 text-sm text-[var(--theme-text-secondary)]">请尝试其他名称或用途关键词。</p>
            <button className="btn-secondary mt-4" onClick={handleClearFilters} type="button">
              清除筛选
            </button>
          </section>
        ) : (
          <>
            <p className="mb-4 text-sm text-[var(--theme-text-secondary)]" aria-live="polite">
              找到 {visibleProfiles.length} 位专家
            </p>
            <section
              aria-label="已发布专家"
              className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,22rem),1fr))] gap-4"
            >
              {visibleProfiles.map((profile) => (
                <ExpertMarketCard
                  key={`${profile.agent_id}:${profile.expected_revision}`}
                  profile={profile}
                  onOpenWorkspace={(selectedProfile) => {
                    void handleOpenWorkspace(selectedProfile);
                  }}
                  onOpenDetail={(selectedProfile) => {
                    const returnSearch = searchParams.toString();
                    navigate(
                      `${buildAgentMarketDetailPath(selectedProfile)}${
                        returnSearch ? `?${returnSearch}` : ""
                      }`,
                    );
                  }}
                />
              ))}
            </section>
          </>
        )}
      </div>
    </main>
  );
}

function AgentMarketDetail({
  profile,
  returnSearch,
}: {
  profile: AgentProfilePublicProjection;
  returnSearch: string;
}) {
  const navigate = useNavigate();
  const handleReturnToCatalog = useCallback(() => {
    navigate(
      returnSearch
        ? `${APP_ROUTE_PATHS.agentMarket}?${returnSearch}`
        : APP_ROUTE_PATHS.agentMarket,
    );
  }, [navigate, returnSearch]);

  return (
    <main
      data-agent-market-detail
      className="min-h-0 flex-1 overflow-y-auto text-[var(--theme-text)]"
    >
      <div className="mx-auto w-full max-w-4xl px-4 py-7 sm:px-6 sm:py-10">
        <button
          aria-label="返回专家市场"
          className="btn-secondary inline-flex items-center gap-2"
          onClick={handleReturnToCatalog}
          type="button"
        >
          <ArrowLeft size={16} aria-hidden="true" />
          返回市场
        </button>

        <section className="mt-7 border-y border-[var(--theme-border)] py-8 sm:py-10">
          <div className="flex flex-col gap-6 sm:flex-row sm:items-start">
            <AgentIdentityAvatar
              agentId={profile.agent_id}
              avatarRef={profile.avatar_ref}
              avatarSeed={profile.avatar_seed}
              name={profile.name}
              size="lg"
            />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2 text-sm font-medium text-[var(--theme-primary)]">
                <span>{AGENT_PROFILE_CATEGORY_LABELS[profile.category]}</span>
                <span className="rounded-full bg-emerald-100 px-2.5 py-1 text-xs text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-200">
                  企业已发布
                </span>
                <span className="rounded-full bg-[var(--theme-bg-sidebar)] px-2.5 py-1 text-xs text-[var(--theme-text-secondary)]">
                  版本 {profile.expected_revision}
                </span>
              </div>
              <h1 className="mt-2 text-2xl font-semibold sm:text-3xl">{profile.name}</h1>
              <p className="mt-4 whitespace-pre-wrap text-sm leading-7 text-[var(--theme-text-secondary)] sm:text-base">
                {profile.capability_summary || profile.description}
              </p>
              {profile.description && profile.description !== profile.capability_summary ? (
                <p className="mt-2 text-sm leading-6 text-[var(--theme-text-secondary)]">
                  {profile.description}
                </p>
              ) : null}
              {profile.published_at ? (
                <p className="mt-4 text-xs text-[var(--theme-text-secondary)]">
                  企业发布时间 {profile.published_at.slice(0, 10)}
                </p>
              ) : null}
              <p className="mt-4 rounded-lg bg-[var(--theme-primary-light)] px-3 py-2 text-sm leading-6 text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]">
                进入后直接描述任务；专家会根据上下文在已发布的 Skill Set 中自主选择能力。
              </p>
            </div>
          </div>
        </section>

        <section className="grid border-b border-[var(--theme-border)] py-7 sm:grid-cols-2 sm:gap-x-10">
          {profile.recommended_tasks.length ? (
            <div className="pb-6 sm:pb-7">
              <h2 className="text-sm font-semibold">适合处理</h2>
              <ul className="mt-3 space-y-2 text-sm leading-6 text-[var(--theme-text-secondary)]">
                {profile.recommended_tasks.map((task) => (
                  <li className="border-l-2 border-emerald-500 pl-3" key={task}>
                    {task}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {profile.starter_prompts.length ? (
            <div className="border-t border-[var(--theme-border)] py-6 sm:border-0 sm:py-0">
              <h2 className="text-sm font-semibold">可以直接开始的任务</h2>
              <ul className="mt-3 space-y-2 text-sm leading-6 text-[var(--theme-text-secondary)]">
                {profile.starter_prompts.map((prompt) => (
                  <li key={prompt}>{prompt}</li>
                ))}
              </ul>
            </div>
          ) : null}
          <div className="border-t border-[var(--theme-border)] py-6">
            <h2 className="text-sm font-semibold">输入与输出</h2>
            <dl className="mt-3 grid grid-cols-[5rem_1fr] gap-x-3 gap-y-2 text-sm leading-6">
              <dt className="text-[var(--theme-text-secondary)]">输入</dt>
              <dd>文本，可按任务附加文件</dd>
              <dt className="text-[var(--theme-text-secondary)]">文件</dt>
              <dd>附件可选，不由专家限定格式</dd>
              <dt className="text-[var(--theme-text-secondary)]">输出</dt>
              <dd>{profile.expected_outputs.join("、") || "对话答复"}</dd>
            </dl>
          </div>
          <div className="border-t border-[var(--theme-border)] py-6">
            <h2 className="text-sm font-semibold">权限与数据访问</h2>
            <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-[var(--theme-text-secondary)]">
              {profile.permissions_and_data_access_notice || "遵循企业当前授权策略。"}
            </p>
          </div>
        </section>

        <div className="flex flex-col gap-3 pt-6 sm:flex-row sm:items-center sm:justify-end">
          <button
            data-agent-market-start-chat
            aria-label={`使用 ${profile.name} 开始任务`}
            className="btn-primary inline-flex min-h-10 shrink-0 items-center justify-center gap-2 px-4"
            onClick={() => navigate(buildAgentMarketWorkspacePath(profile))}
            type="button"
          >
            <MessageCircle size={17} aria-hidden="true" />
            开始任务
          </button>
        </div>
      </div>
    </main>
  );
}

/** Published Expert catalog and exact revision detail route. */
export function AgentMarketRoute() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { agentId, revision } = useParams<{ agentId?: string; revision?: string }>();
  const isDetailRoute = agentId !== undefined || revision !== undefined;
  const searchQuery = searchParams.get("q")?.trim() || undefined;
  const requestedCategory = searchParams.get("category");
  const activeCategory = MARKET_CATEGORIES.some(
    (category) => category.value === requestedCategory,
  )
    ? (requestedCategory as AgentProfileCategory | "all")
    : "all";
  const catalogCategory =
    activeCategory === "all" ? undefined : activeCategory;
  const catalogKey = `catalog:${searchQuery ?? ""}:${catalogCategory ?? "all"}`;
  const { catalog, refresh: refreshCatalog } = usePublishedAgentCatalog(
    catalogKey,
    searchQuery,
    catalogCategory,
    !isDetailRoute,
  );
  const detailKey = `detail:${agentId ?? ""}:${revision ?? ""}`;
  const { detail, refresh: refreshDetail } = usePublishedAgentDetail(
    detailKey,
    agentId,
    revision,
    isDetailRoute,
  );
  const returnSearch = searchParams.toString();
  const catalogReturnPath = returnSearch
    ? `${APP_ROUTE_PATHS.agentMarket}?${returnSearch}`
    : APP_ROUTE_PATHS.agentMarket;
  useEffect(() => {
    if (isDetailRoute && detail.phase === "unavailable") {
      navigate(catalogReturnPath, { replace: true });
    }
  }, [catalogReturnPath, detail.phase, isDetailRoute, navigate]);

  if (!isDetailRoute) {
    return (
      <AgentMarketShell>
        <AgentMarketCatalog
          activeCategory={activeCategory}
          catalog={catalog}
          refresh={refreshCatalog}
        />
      </AgentMarketShell>
    );
  }

  return (
    <AgentMarketShell>
      {detail.phase === "error" ? (
        <main className="min-h-0 flex-1 overflow-y-auto px-4 py-10 text-[var(--theme-text)] sm:px-6">
          <div className="mx-auto max-w-2xl">
            <CatalogError error={detail.error ?? MARKET_CATALOG_LOAD_ERROR} refresh={refreshDetail} />
          </div>
        </main>
      ) : detail.phase === "ready" && detail.value ? (
            <AgentMarketDetail
              profile={detail.value}
              returnSearch={returnSearch}
            />
      ) : (
        <main
          aria-live="polite"
          className="min-h-0 flex-1 overflow-y-auto px-4 py-10 text-sm text-[var(--theme-text-secondary)] sm:px-6"
        >
          <div className="mx-auto max-w-2xl">正在校验当前发布版本…</div>
        </main>
      )}
    </AgentMarketShell>
  );
}
