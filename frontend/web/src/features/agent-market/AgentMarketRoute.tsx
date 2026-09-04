import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  ArrowLeft,
  Grid2X2,
  List as ListIcon,
  MessageCircle,
  RefreshCw,
  Search,
  Star,
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
  buildAgentMarketDetailPath,
  buildAgentMarketWorkspacePath,
  filterPublishedMarketProfiles,
  selectPublishedMarketProfile,
} from "./agentMarketSelection";
import { AgentIdentityAvatar } from "../../components/agent/AgentIdentityAvatar";
import { Pagination } from "../../components/common/Pagination";

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
const MARKET_PAGE_SIZE = 9;
type MarketView = "grid" | "list";
type MarketSort = "default" | "tasks" | "recent";

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
  enabled: boolean,
) {
  const [retry, setRetry] = useState(0);
  const [catalog, setCatalog] = useState<CatalogState>(() => loadState(catalogKey, []));

  useEffect(() => {
    if (!enabled) return;
    let active = true;
    setCatalog(loadState(catalogKey, []));
    void agentProfileApi
      .listPublished()
      .then((response) => {
        if (active)
          setCatalog(loadState(catalogKey, response.agent_profiles, "ready"));
      })
      .catch(() => {
        if (active)
          setCatalog(loadState(catalogKey, [], "error", MARKET_CATALOG_LOAD_ERROR));
      });
    return () => { active = false; };
  }, [catalogKey, enabled, retry]);

  const refresh = useCallback(() => setRetry((current) => current + 1), []);
  const toggleFavorite = useCallback(async (profile: AgentProfilePublicProjection) => {
    const updated = await agentProfileApi.setFavorite(profile.agent_id, !profile.is_favorite);
    setCatalog((current) => current.key !== catalogKey || current.phase !== "ready"
      ? current
      : { ...current, value: current.value.map((item) =>
        item.agent_id === updated.agent_id ? updated : item,
      ) });
  }, [catalogKey]);
  return {
    catalog: catalog.key === catalogKey ? catalog : loadState(catalogKey, []),
    refresh,
    toggleFavorite,
  };
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
  view,
  onOpenWorkspace,
  onOpenDetail,
  onToggleFavorite,
}: {
  profile: AgentProfilePublicProjection;
  view: MarketView;
  onOpenWorkspace: (profile: AgentProfilePublicProjection) => void;
  onOpenDetail: (profile: AgentProfilePublicProjection) => void;
  onToggleFavorite: (profile: AgentProfilePublicProjection) => void;
}) {
  const completedTasks = profile.completed_tasks ?? 0;
  const listView = view === "list";
  return (
    <article
      data-agent-market-card
      className={`group flex min-w-0 flex-col overflow-hidden rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] shadow-[0_1px_3px_rgba(15,23,42,0.05)] transition-[border-color,box-shadow] hover:border-[var(--theme-primary)] hover:shadow-[0_8px_24px_rgba(15,23,42,0.08)] ${
        listView ? "sm:flex-row" : "min-h-72"
      }`}
    >
      <div className={`flex min-w-0 flex-1 flex-col p-5 ${listView ? "sm:min-w-0 sm:flex-row sm:gap-6" : ""}`}>
        <div className="flex min-w-0 flex-1 flex-col">
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
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    aria-label={profile.is_favorite ? `取消收藏 ${profile.name}` : `收藏 ${profile.name}`}
                    className={`rounded-full p-1 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-primary)] ${
                      profile.is_favorite
                        ? "text-amber-600 dark:text-amber-300"
                        : "text-[var(--theme-text-secondary)] hover:text-amber-500"
                    }`}
                    onClick={() => onToggleFavorite(profile)}
                    type="button"
                  >
                    <Star aria-hidden="true" fill={profile.is_favorite ? "currentColor" : "none"} size={17} />
                  </button>
                </div>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-[var(--theme-text-secondary)]">
                <span>{profile.market_tag || "未分类"}</span>
                <span aria-hidden="true">·</span>
                <span>企业已发布</span>
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

          <div className="mt-5 flex items-center gap-3 border-t border-[var(--theme-border)] pt-3 text-xs">
            <span className="text-[var(--theme-text-secondary)]">已完成任务</span>
            <strong className="tabular-nums text-[var(--theme-text)]">
              {completedTasks.toLocaleString("zh-CN")}
            </strong>
          </div>
        </div>
      </div>
      <div
        className={`grid gap-3 border-t border-[var(--theme-border)] p-4 sm:p-5 ${
          listView ? "sm:w-64 sm:grid-cols-1 sm:border-l sm:border-t-0" : "grid-cols-2"
        }`}
      >
        <button
          data-agent-market-open-detail
          aria-label={`查看 ${profile.name} 详情`}
          className="btn-secondary inline-flex min-h-10 items-center justify-center px-3 text-sm"
          onClick={() => onOpenDetail(profile)}
          type="button"
        >
          查看详情
        </button>
        <button
          data-agent-market-open-workspace
          aria-label={`使用 ${profile.name} 开始合作`}
          className="btn-primary inline-flex min-h-10 items-center justify-center px-3 text-sm"
          onClick={() => onOpenWorkspace(profile)}
          type="button"
        >
          开始合作
        </button>
      </div>
    </article>
  );
}

function AgentMarketCatalog({
  catalog,
  refresh,
  activeTag,
  activeTab,
  toggleFavorite,
}: {
  catalog: CatalogState;
  refresh: () => void;
  activeTag: string | null;
  activeTab: "tags" | "favorites";
  toggleFavorite: (profile: AgentProfilePublicProjection) => Promise<void>;
}) {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const searchQuery = searchParams.get("q") ?? "";
  const [searchInput, setSearchInput] = useState(searchQuery);
  const isSearchComposing = useRef(false);
  const [page, setPage] = useState(1);
  const [view, setView] = useState<MarketView>("grid");
  const [sort, setSort] = useState<MarketSort>("default");
  const [tagSearch, setTagSearch] = useState("");
  useEffect(() => {
    if (!isSearchComposing.current) setSearchInput(searchQuery);
  }, [searchQuery]);
  const tagCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const profile of catalog.value) {
      const tag = profile.market_tag?.trim();
      if (tag) counts.set(tag, (counts.get(tag) ?? 0) + 1);
    }
    return counts;
  }, [catalog.value]);
  const marketTags = useMemo(
    () => [...tagCounts.keys()].sort((left, right) =>
      (tagCounts.get(right) ?? 0) - (tagCounts.get(left) ?? 0) || left.localeCompare(right, "zh-CN"),
    ),
    [tagCounts],
  );
  const visibleTagSearch = tagSearch.trim().toLocaleLowerCase();
  const filteredMarketTags = marketTags.filter((tag) =>
    !visibleTagSearch || tag.toLocaleLowerCase().includes(visibleTagSearch),
  );
  const hotTags = marketTags.slice(0, 7);
  const hasActiveFilter =
    searchQuery.trim().length > 0 || (activeTab === "tags" && activeTag !== null);
  const visibleProfiles = useMemo(() => {
    const filtered = filterPublishedMarketProfiles(catalog.value, searchQuery).filter((profile) =>
      (activeTab !== "favorites" || profile.is_favorite) &&
      (activeTab !== "tags" || activeTag === null || profile.market_tag === activeTag),
    );
    return [...filtered].sort((left, right) => {
      if (sort === "tasks") {
        return (right.completed_tasks ?? 0) - (left.completed_tasks ?? 0) || left.name.localeCompare(right.name, "zh-CN");
      }
      if (sort === "recent") {
        return (Date.parse(String(right.published_at ?? "")) || 0) - (Date.parse(String(left.published_at ?? "")) || 0)
          || left.name.localeCompare(right.name, "zh-CN");
      }
      return 0;
    });
  }, [activeTab, activeTag, catalog.value, searchQuery, sort]);

  const pageCount = Math.max(1, Math.ceil(visibleProfiles.length / MARKET_PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);
  const paginatedProfiles = useMemo(
    () => visibleProfiles.slice(
      (currentPage - 1) * MARKET_PAGE_SIZE,
      currentPage * MARKET_PAGE_SIZE,
    ),
    [currentPage, visibleProfiles],
  );

  useEffect(() => {
    if (page !== currentPage) setPage(currentPage);
  }, [currentPage, page]);

  const handleOpenWorkspace = useCallback(
    (profile: AgentProfilePublicProjection) => {
      navigate(buildAgentMarketWorkspacePath(profile));
    },
    [navigate],
  );

  const handleTag = useCallback(
    (tag: string | null) => {
      const next = new URLSearchParams(searchParams);
      if (tag === null) next.delete("tag");
      else {
        next.set("tag", tag);
        next.delete("tab");
      }
      setPage(1);
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const handleTab = useCallback(
    (tab: "tags" | "favorites") => {
      const next = new URLSearchParams(searchParams);
      if (tab === "tags") next.delete("tab");
      else next.set("tab", "favorites");
      if (tab === "favorites") next.delete("tag");
      setPage(1);
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const handleSearch = useCallback(
    (query: string) => {
      const next = new URLSearchParams(searchParams);
      if (query) next.set("q", query);
      else next.delete("q");
      setPage(1);
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const handleSearchInput = useCallback(
    (query: string) => {
      setSearchInput(query);
      if (!isSearchComposing.current) handleSearch(query);
    },
    [handleSearch],
  );

  const handleRefresh = useCallback(() => {
    refresh();
  }, [refresh]);
  const handleSort = useCallback((nextSort: MarketSort) => {
    setSort(nextSort);
    setPage(1);
  }, []);
  const handleView = useCallback((nextView: MarketView) => {
    setView(nextView);
  }, []);
  const handleClearFilters = useCallback(() => {
    setPage(1);
    setSort("default");
    setTagSearch("");
    setSearchParams(new URLSearchParams(), { replace: true });
  }, [setSearchParams]);

  return (
    <main data-agent-market className="min-h-0 flex-1 overflow-y-auto bg-[var(--theme-workbench-canvas)] text-[var(--theme-text)]">
      <div className="flex w-full flex-col px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
        <header className="flex flex-col gap-4 lg:flex-row lg:items-center lg:gap-7">
          <div className="min-w-0 lg:w-60 lg:shrink-0">
            <div className="flex items-center gap-2">
              <h1 className="text-2xl font-semibold">专家市场</h1>
            </div>
            <p className="mt-1 text-sm text-[var(--theme-text-secondary)]">找到最适合您项目的专家伙伴</p>
          </div>
          <div className="relative min-w-0 flex-1">
            <div className="flex items-center gap-2 lg:absolute lg:right-0 lg:top-0">
              <button
                data-agent-market-favorites
                aria-label="查看我的收藏"
                aria-pressed={activeTab === "favorites"}
                className={`inline-flex h-10 items-center gap-2 rounded-md border px-3 text-sm transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-primary)] ${
                  activeTab === "favorites"
                    ? "border-[var(--theme-primary)] bg-[var(--theme-primary)] text-white"
                    : "border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] text-[var(--theme-text-secondary)] hover:border-[var(--theme-primary)] hover:text-[var(--theme-primary)]"
                }`}
                onClick={() => handleTab(activeTab === "favorites" ? "tags" : "favorites")}
                type="button"
              >
                <Star aria-hidden="true" fill={activeTab === "favorites" ? "currentColor" : "none"} size={15} />
                我的收藏
              </button>
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
            </div>
            <form
              className="mt-4 flex h-10 w-full overflow-hidden rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] focus-within:border-[var(--theme-primary)] focus-within:ring-2 focus-within:ring-[var(--theme-primary)]/20 lg:mx-auto lg:mt-0 lg:max-w-[43rem]"
              onSubmit={(event) => {
                event.preventDefault();
                handleSearch(searchInput);
              }}
            >
              <label className="sr-only" htmlFor="agent-market-search">搜索专家</label>
              <input
                id="agent-market-search"
                data-agent-market-search
                aria-label="搜索专家"
                className="min-w-0 flex-1 bg-transparent px-4 text-sm text-[var(--theme-text)] outline-none placeholder:text-[var(--theme-text-secondary)]"
                maxLength={160}
                onChange={(event) => handleSearchInput(event.target.value)}
                onCompositionStart={() => {
                  isSearchComposing.current = true;
                }}
                onCompositionEnd={(event) => {
                  isSearchComposing.current = false;
                  const query = event.currentTarget.value;
                  setSearchInput(query);
                  handleSearch(query);
                }}
                placeholder="搜索专家名称、能力或任务…"
                type="search"
                value={searchInput}
              />
              <button
                aria-label="提交搜索"
                className="inline-flex w-12 shrink-0 items-center justify-center bg-[var(--theme-primary)] text-white transition-colors hover:brightness-95 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-white"
                type="submit"
              >
                <Search aria-hidden="true" size={17} />
              </button>
            </form>
          </div>
        </header>

        <div className="mt-6 grid gap-7 lg:grid-cols-[15rem_minmax(0,1fr)]">
          <aside className="h-fit rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-4">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-sm font-semibold">筛选条件</h2>
              <button
                className="text-xs text-[var(--theme-primary)] hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-primary)]"
                onClick={handleClearFilters}
                type="button"
              >
                重置
              </button>
            </div>
            <div className="mt-4 border-t border-[var(--theme-border)] pt-4">
              <div className="flex items-center justify-between gap-2">
                <h3 className="text-xs font-semibold">标签筛选</h3>
              </div>
              <label className="relative mt-3 block">
                <span className="sr-only">搜索标签</span>
                <Search
                  aria-hidden="true"
                  className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--theme-text-secondary)]"
                  size={14}
                />
                <input
                  aria-label="搜索标签"
                  className="h-9 w-full rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] pl-8 pr-2 text-xs text-[var(--theme-text)] outline-none placeholder:text-[var(--theme-text-secondary)] focus:border-[var(--theme-primary)] focus:ring-2 focus:ring-[var(--theme-primary)]/20"
                  onChange={(event) => setTagSearch(event.target.value)}
                  placeholder="搜索标签…"
                  type="search"
                  value={tagSearch}
                />
              </label>
              <div
                data-agent-market-filter
                aria-label="市场标签"
                className="mt-3 space-y-1"
                role="group"
              >
                <button
                  aria-pressed={activeTab === "tags" && activeTag === null}
                  className={`flex min-h-8 w-full items-center gap-2 rounded-md px-2 text-left text-xs transition-colors ${
                    activeTab === "tags" && activeTag === null
                      ? "bg-[var(--theme-primary)] text-white"
                      : "text-[var(--theme-text-secondary)] hover:bg-[var(--theme-bg-sidebar)] hover:text-[var(--theme-text)]"
                  }`}
                  onClick={() => handleTag(null)}
                  type="button"
                >
                  <span>全部</span>
                </button>
                {filteredMarketTags.map((tag) => (
                  <button
                    aria-pressed={activeTab === "tags" && activeTag === tag}
                    className={`flex min-h-8 w-full items-center gap-2 rounded-md px-2 text-left text-xs transition-colors ${
                      activeTab === "tags" && activeTag === tag
                        ? "bg-[var(--theme-primary)] text-white"
                        : "text-[var(--theme-text-secondary)] hover:bg-[var(--theme-bg-sidebar)] hover:text-[var(--theme-text)]"
                    }`}
                    key={tag}
                    onClick={() => handleTag(tag)}
                    type="button"
                  >
                    <span className="min-w-0 flex-1 truncate">{tag}</span>
                    <span className="tabular-nums text-[11px] opacity-70">{tagCounts.get(tag)}</span>
                  </button>
                ))}
              </div>
              {marketTags.length === 0 ? (
                <p className="mt-3 text-xs text-[var(--theme-text-secondary)]">暂无可用标签</p>
              ) : null}
            </div>
          </aside>

          <section className="min-w-0">
            <div className="rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-4 sm:p-5">
              <div className="flex items-center justify-between gap-3">
                <h2 className="text-sm font-semibold">热门标签</h2>
                <span className="text-xs text-[var(--theme-text-secondary)]">{marketTags.length} 个标签</span>
              </div>
              <div className="mt-3 flex gap-2 overflow-x-auto pb-1">
                <button
                  aria-pressed={activeTab === "tags" && activeTag === null}
                  className={`inline-flex min-h-9 shrink-0 items-center gap-1.5 rounded-full border px-3 text-xs transition-colors ${
                    activeTab === "tags" && activeTag === null
                      ? "border-[var(--theme-primary)] bg-[var(--theme-primary)] text-white"
                      : "border-[var(--theme-border)] text-[var(--theme-text-secondary)] hover:border-[var(--theme-primary)] hover:text-[var(--theme-primary)]"
                  }`}
                  onClick={() => handleTag(null)}
                  type="button"
                >
                  全部
                </button>
                {hotTags.map((tag) => (
                  <button
                    aria-pressed={activeTab === "tags" && activeTag === tag}
                    className={`inline-flex min-h-9 shrink-0 items-center gap-1.5 rounded-full border px-3 text-xs transition-colors ${
                      activeTab === "tags" && activeTag === tag
                        ? "border-[var(--theme-primary)] bg-[var(--theme-primary)] text-white"
                        : "border-[var(--theme-border)] text-[var(--theme-text-secondary)] hover:border-[var(--theme-primary)] hover:text-[var(--theme-primary)]"
                    }`}
                    key={tag}
                    onClick={() => handleTag(tag)}
                    type="button"
                  >
                    {tag}
                    <span className="tabular-nums opacity-70">{tagCounts.get(tag)}</span>
                  </button>
                ))}
              </div>
            </div>

            <div className="mt-4 flex flex-wrap items-center gap-3">
              <div aria-label="排序方式" className="flex flex-wrap items-center gap-2" data-agent-market-sort role="group">
                <span className="text-xs text-[var(--theme-text-secondary)]">排序方式</span>
                {([
                  ["default", "综合排序"],
                  ["tasks", "完成任务最多"],
                  ["recent", "最近发布"],
                ] as const).map(([sortValue, label]) => (
                  <button
                    aria-pressed={sort === sortValue}
                    className={`min-h-8 rounded-md border px-3 text-xs transition-colors ${
                      sort === sortValue
                        ? "border-[var(--theme-primary)] bg-[var(--theme-primary-light)] text-[var(--theme-primary)]"
                        : "border-[var(--theme-border)] text-[var(--theme-text-secondary)] hover:border-[var(--theme-primary)] hover:text-[var(--theme-primary)]"
                    }`}
                    key={sortValue}
                    onClick={() => handleSort(sortValue)}
                    type="button"
                  >
                    {label}
                  </button>
                ))}
              </div>
              <div className="ml-auto flex items-center gap-2">
                <span className="text-xs text-[var(--theme-text-secondary)]">共 {visibleProfiles.length} 位专家</span>
                <div aria-label="专家展示方式" className="flex rounded-md border border-[var(--theme-border)]" data-agent-market-view role="group">
                  <button
                    aria-label="网格视图"
                    aria-pressed={view === "grid"}
                    className={`inline-flex h-8 w-9 items-center justify-center rounded-l-md ${view === "grid" ? "bg-[var(--theme-primary)] text-white" : "text-[var(--theme-text-secondary)] hover:bg-[var(--theme-bg-sidebar)]"}`}
                    onClick={() => handleView("grid")}
                    type="button"
                  >
                    <Grid2X2 aria-hidden="true" size={15} />
                  </button>
                  <button
                    aria-label="列表视图"
                    aria-pressed={view === "list"}
                    className={`inline-flex h-8 w-9 items-center justify-center rounded-r-md border-l border-[var(--theme-border)] ${view === "list" ? "bg-[var(--theme-primary)] text-white" : "text-[var(--theme-text-secondary)] hover:bg-[var(--theme-bg-sidebar)]"}`}
                    onClick={() => handleView("list")}
                    type="button"
                  >
                    <ListIcon aria-hidden="true" size={15} />
                  </button>
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
            <h2 className="text-base font-semibold">
              {activeTab === "favorites" ? "尚未收藏专家" : "没有匹配的专家"}
            </h2>
            <p className="mt-2 text-sm text-[var(--theme-text-secondary)]">
              {activeTab === "favorites" ? "在专家卡片上点击星标即可收藏专家。" : "请尝试其他名称或标签。"}
            </p>
            <button className="btn-secondary mt-4" onClick={handleClearFilters} type="button">
              清除筛选
            </button>
          </section>
        ) : (
          <>
            <section
              aria-label="已发布专家"
              data-agent-market-catalog
              className={view === "grid" ? "mt-4 grid grid-cols-[repeat(auto-fill,minmax(min(100%,18rem),1fr))] gap-4" : "mt-4 flex flex-col gap-4"}
            >
              {paginatedProfiles.map((profile) => (
                <ExpertMarketCard
                  key={`${profile.agent_id}:${profile.expected_revision}`}
                  profile={profile}
                  view={view}
                  onToggleFavorite={(selectedProfile) => {
                    void toggleFavorite(selectedProfile).catch(handleRefresh);
                  }}
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
            <div className="mt-6">
              <Pagination
                page={currentPage}
                pageSize={MARKET_PAGE_SIZE}
                total={visibleProfiles.length}
                onChange={setPage}
              />
            </div>
          </>
        )}
          </section>
        </div>
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
                <span>{profile.market_tag || "未分类"}</span>
                <span className="rounded-full bg-emerald-100 px-2.5 py-1 text-xs text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-200">
                  企业已发布
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
  const requestedTag = searchParams.get("tag")?.trim();
  const activeTag = requestedTag || null;
  const activeTab = searchParams.get("tab") === "favorites" ? "favorites" : "tags";
  const catalogKey = "catalog";
  const {
    catalog,
    refresh: refreshCatalog,
    toggleFavorite,
  } = usePublishedAgentCatalog(
    catalogKey,
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
          activeTab={activeTab}
          activeTag={activeTag}
          catalog={catalog}
          refresh={refreshCatalog}
          toggleFavorite={toggleFavorite}
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
