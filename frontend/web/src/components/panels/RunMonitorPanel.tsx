import { Button, Input } from "@lobehub/ui/base-ui";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleX,
  Pause,
  Play,
  RefreshCw,
  Search,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, Navigate, useLocation, useParams, useSearchParams } from "react-router-dom";
import { PanelHeader } from "../common/PanelHeader";
import { Table } from "@lobehub/ui/base-ui";
import { PanelLoadingState } from "../common/PanelLoadingState";
import { WorkbenchStateSurface } from "../workbench/WorkbenchStateSurface";
import { workbenchSurface } from "../workbench/workbenchSurface";
import {
  adminRunsApi,
  readAdminRunDeepLinkScope,
  type AdminRunSummary,
  type AdminRunMonitorResponse,
} from "../../services/api/adminRuns";
import { agentDisplayName, compactIdentifier, dateTime, durationLabel, runDisplayTitle, skillDisplayName, userDisplayName, workspaceDisplayName } from "./runPresentation";
import { RunQuickView } from "./RunQuickView";
import { StatusBadge } from "./RunDetailFacts";
import { RunDetailPage } from "./RunDetailPage";
import { runDetailUrl } from "./runDetailNavigation";
import { LobeSelectField } from "../lobe-ui/LobeSelectField";
import { RunPerformanceSummary } from "./RunPerformanceSummary";
import { filterAdminRuns } from "./adminRunTimeline";
import { registerAuthScopedCacheClearer } from "../../services/api/authCacheInvalidation";

const listScrollPositions = new Map<string, number>();
registerAuthScopedCacheClearer(() => listScrollPositions.clear());

const RUN_LIMIT = 50;
const PAGE_SIZE = 10;
const AUTO_REFRESH_INTERVAL_MS = 5_000;

const STATUS_FILTERS = [
  { value: "all", label: "全部" },
  { value: "queued", label: "排队" },
  { value: "running", label: "执行中" },
  { value: "succeeded", label: "成功" },
  { value: "failed", label: "失败" },
  { value: "cancelled", label: "已取消" },
] as const;

type StatusFilter = (typeof STATUS_FILTERS)[number]["value"];

function statusCount(filter: StatusFilter, report: AdminRunMonitorResponse | null): number | null {
  return !report ? null : filter === "all" ? report.counts.total : report.counts[filter] ?? null;
}

function DesktopRunTable({
  runs,
  selectedRunId,
  onSelect,
}: {
  runs: AdminRunSummary[];
  selectedRunId: string | null;
  onSelect: (runId: string) => void;
}) {
  return (
    <div className="hidden min-w-[940px] md:block">
      <Table
 size="small" pagination={false} tableLayout="fixed"
 dataSource={runs}
 rowKey={(run) => run.run_id}
onRow={(run) => { const selected = run.run_id === selectedRunId; return ({"data-selected": selected ? "true" : "false",
"className": "group transition-colors hover:bg-[var(--theme-bg-sidebar)] data-[selected=true]:bg-[var(--theme-info-soft)]"}); }}
 columns={[
{ key: "status", title: "状态", width: "8%",
render: (_value, run) => (<>
<StatusBadge status={run.status} />
                  {run.status === "queued" && run.queue_position ? (
                    <p className="mt-1.5 whitespace-nowrap text-[11px] text-[var(--theme-text-tertiary)]">
                      队列第 {run.queue_position} 位
                    </p>
                  ) : null}
                  {run.error_code ? (
                    <p
                      className="mt-1.5 max-w-40 truncate font-mono text-[11px] text-[var(--theme-danger-text)]"
                      title={run.error_code}
                    >
                      {run.error_code}
                    </p>
                  ) : null}
</>) },
{ key: "identity", title: "任务 / 运行", width: "24%",
render: (_value, run) => (<>
<Link
                    to={runDetailUrl(run.run_id, window.location.search)}
                    className="block w-full min-w-0 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-focus-ring)]"
                    aria-label={`查看 ${run.run_id}`}
                  >
                    <span className="block w-full min-w-0 text-left">
                    <span
                      className="block truncate font-medium text-[var(--theme-text)]"
                      title={runDisplayTitle(run)}
                    >
                      {runDisplayTitle(run)}
                    </span>
                    <span
                      className="mt-1 block truncate font-mono text-[11px] text-[var(--theme-text-tertiary)]"
                      title={`Run ID: ${run.run_id}`}
                    >
                      运行 {compactIdentifier(run.run_id)}
                    </span>
                    </span>
                  </Link>
</>) },
{ key: "user", title: "用户 / 工作区", width: "16%",
render: (_value, run) => (<>
<p className="truncate text-[var(--theme-text)]" title={userDisplayName(run)}>
                    {userDisplayName(run)}
                  </p>
                  <p
                    className="mt-1 truncate text-[11px] text-[var(--theme-text-tertiary)]"
                    title={workspaceDisplayName(run)}
                  >
                    {workspaceDisplayName(run)}
                  </p>
</>) },
{ key: "agent", title: "Agent / Skill", width: "18%",
render: (_value, run) => (<>
<p className="truncate text-[var(--theme-text)]" title={agentDisplayName(run)}>
                    {agentDisplayName(run)}
                  </p>
                  <p
                    className="mt-1 truncate text-[11px] text-[var(--theme-text-tertiary)]"
                    title={skillDisplayName(run)}
                  >
                    {skillDisplayName(run)}
                  </p>
</>) },
{ key: "time", title: "提交时间", width: "18%",
render: (_value, run) => <span className="whitespace-nowrap">{dateTime(run.created_at)}</span> },
{ key: "duration", title: "耗时", width: "9%",
render: (_value, run) => <span className="whitespace-nowrap tabular-nums">{durationLabel(run)}</span> },
{ key: "actions", title: "操作", width: "7%",
render: (_value, run) => (<>
<Button type="text"
                    htmlType="button"
                    onClick={() => onSelect(run.run_id)}
                    className="font-medium text-[var(--theme-primary)]"
                    aria-label={`快速查看 ${run.run_id}`}
                    title="快速查看"
>快速查看</Button>
</>) }
]}
/>
    </div>
  );
}

function MobileRunList({
  runs,
  selectedRunId,
  onSelect,
}: {
  runs: AdminRunSummary[];
  selectedRunId: string | null;
  onSelect: (runId: string) => void;
}) {
  return (
    <div className="divide-y divide-[var(--theme-border)] md:hidden">
      {runs.map((run) => (
        <div
          key={run.run_id}
          data-selected={run.run_id === selectedRunId ? "true" : "false"}
          className="block w-full px-3 py-3 text-left transition-colors hover:bg-[var(--theme-bg-sidebar)] data-[selected=true]:bg-[var(--theme-info-soft)]"
        >
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <Link to={runDetailUrl(run.run_id, window.location.search)} aria-label={`查看 ${run.run_id}`} className="block truncate text-sm font-medium text-[var(--theme-text)]">{runDisplayTitle(run)}</Link>
              <p className="mt-1 truncate font-mono text-[11px] text-[var(--theme-text-tertiary)]">
                运行 {compactIdentifier(run.run_id)}
              </p>
            </div>
            <StatusBadge status={run.status} />
          </div>
          {run.error_code ? (
            <p className="mt-2 truncate font-mono text-[11px] text-[var(--theme-danger-text)]">
              {run.error_code}
            </p>
          ) : null}
          <div className="mt-2 flex items-center justify-between gap-3 text-[11px] text-[var(--theme-text-secondary)]">
            <span className="truncate">{userDisplayName(run)} · {agentDisplayName(run)}</span>
            <span className="shrink-0 tabular-nums">{durationLabel(run)}</span>
          </div>
          <Button type="text" htmlType="button" size="small" aria-label={`快速查看 ${run.run_id}`} onClick={() => onSelect(run.run_id)}>快速查看</Button>
        </div>
      ))}
    </div>
  );
}

export function RunMonitorPanel() {
  const { runId } = useParams<{ runId: string }>();
  const location = useLocation();
  const scope = readAdminRunDeepLinkScope(location.search);
  if (runId) return <RunDetailPage key={runId} runId={runId} />;
  if (scope.runId) return <Navigate replace to={runDetailUrl(scope.runId, location.search)} />;
  return <RunMonitorList />;
}

export function RunMonitorList() {
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const scope = useMemo(() => readAdminRunDeepLinkScope(location.search), [location.search]);
  const [runs, setRuns] = useState<AdminRunSummary[]>([]);
  const [monitorReport, setMonitorReport] = useState<AdminRunMonitorResponse | null>(null);
  const hours = [1, 24, 168].includes(Number(params.get("hours"))) ? Number(params.get("hours")) : 24;
  const modelFilter = params.get("model") || "all";
  const setModelFilter = (value: string) => setParams((current) => { if (value === "all") current.delete("model"); else current.set("model", value); current.delete("page"); return current; }, { replace: true });
  const agentFilter = params.get("agent_id") ? { id: params.get("agent_id")!, label: params.get("agent_id")! } : null;
  const agentId = agentFilter?.id;
  const setAgentFilter = (value: { id: string; label: string } | null) => setParams((current) => { if (value) current.set("agent_id", value.id); else current.delete("agent_id"); current.delete("page"); return current; }, { replace: true });
  const order = params.get("order") === "slow" ? "slow" : "recent";
  const setOrder = (value: string) => setParams((current) => { current.set("order", value); current.delete("page"); return current; }, { replace: true });
  const statusFilter: StatusFilter = STATUS_FILTERS.find((item) => item.value === params.get("status"))?.value ?? "all";
  const setStatusFilter = (value: StatusFilter) => setParams((current) => { if (value === "all") current.delete("status"); else current.set("status", value); current.delete("page"); return current; }, { replace: true });
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(scope.runId);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null);
  const page = Math.max(1, Number(params.get("page")) || 1);
  const setPage = (value: number | ((current: number) => number)) => setParams((current) => { const next = typeof value === "function" ? value(Math.max(1, Number(current.get("page")) || 1)) : value; if (next === 1) current.delete("page"); else current.set("page", String(next)); return current; }, { replace: true });
  const listScroll = useRef<HTMLElement>(null);
  const scrollPositions = useRef(listScrollPositions);
  const listRequestSequence = useRef(0);
  const listRequestInFlight = useRef<number | null>(null);
  const refreshButtonRef = useRef<HTMLButtonElement>(null);

  const loadRuns = useCallback(async (initial = false) => {
    if (!initial && listRequestInFlight.current !== null) return;
    const requestId = ++listRequestSequence.current;
    listRequestInFlight.current = requestId;
    if (initial) setIsLoading(true);
    else setIsRefreshing(true);
    try {
      const [modelId, revision] = modelFilter === "all" ? [] : modelFilter.split("@");
      const response = await adminRunsApi.monitor({
        limit: RUN_LIMIT, hours, order,
        userId: scope.userId ?? undefined,
        status: statusFilter === "all" ? undefined : statusFilter,
        agentId, modelId,
        modelGatewayRevision: revision ? Number(revision) : undefined,
      });
      if (requestId !== listRequestSequence.current) return;
      setMonitorReport(response);
      setRuns(response.runs);
      setLoadError(null);
      setLastUpdatedAt(new Date());

    } catch (error) {
      if (requestId !== listRequestSequence.current) return;
      setLoadError(error instanceof Error ? error.message : "最近运行加载失败");
    } finally {
      if (listRequestInFlight.current === requestId) listRequestInFlight.current = null;
      if (requestId === listRequestSequence.current) {
        setIsLoading(false);
        setIsRefreshing(false);
      }
    }
  }, [scope.userId, hours, modelFilter, agentId, statusFilter, order]);

  useEffect(() => {
    void loadRuns(true);
  }, [loadRuns]);

  useEffect(() => {
    if (!autoRefresh) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void loadRuns(false);
    }, AUTO_REFRESH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [autoRefresh, loadRuns]);

  useEffect(() => () => {
    listRequestSequence.current += 1;
  }, []);

  const closeDetail = useCallback(() => setSelectedRunId(null), []);
  const selectRun = useCallback((runId: string) => setSelectedRunId(runId), []);

  const invalidateMonitorScope = useCallback(() => {
    listRequestSequence.current += 1;
    closeDetail();
    setMonitorReport(null);
    setRuns([]);
    setIsLoading(true);
    setLoadError(null);
  }, [closeDetail]);

  const filteredRuns = useMemo(
    () => filterAdminRuns(runs, statusFilter, searchQuery),
    [runs, searchQuery, statusFilter],
  );
  const pageCount = Math.max(1, Math.ceil(filteredRuns.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);
  const visibleRuns = useMemo(
    () => filteredRuns.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE),
    [currentPage, filteredRuns],
  );
  useEffect(() => {
    if (!isLoading && listScroll.current) listScroll.current.scrollTop = scrollPositions.current.get(location.search) || 0;
  }, [isLoading, location.search]);

  const lastUpdatedLabel = lastUpdatedAt
    ? lastUpdatedAt.toLocaleTimeString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" })
    : "尚未刷新";

  const headerActions = (
    <div className="flex flex-wrap items-center gap-1">
      <span className="mr-2 text-[11px] text-[var(--theme-text-tertiary)]">{autoRefresh ? "每 5 秒自动刷新" : "自动刷新已暂停"} · {lastUpdatedLabel}</span>
      <Button type="text"
        htmlType="button"
        className="flex size-9 items-center justify-center rounded-md"
        onClick={() => setAutoRefresh((value) => !value)}
        aria-label={autoRefresh ? "暂停自动刷新" : "开启自动刷新"}
        title={autoRefresh ? "暂停自动刷新" : "开启自动刷新"}
        icon={autoRefresh ? <Pause size={16} /> : <Play size={16} />}
      />
      <Button type="text"
        ref={refreshButtonRef}
        htmlType="button"
        className="flex size-9 items-center justify-center rounded-md"
        onClick={() => void loadRuns(false)}
        disabled={isRefreshing}
        aria-label="刷新最近运行"
        title="刷新最近运行"
        icon={<RefreshCw size={16} className={isRefreshing ? "animate-spin" : ""} />}
      />
    </div>
  );

  if (isLoading && runs.length === 0) {
    return (
      <div className={workbenchSurface.statePage}>
        <PanelLoadingState text="正在读取最近 Worker 请求" />
      </div>
    );
  }

  if (loadError && runs.length === 0) {
    return (
      <div className={workbenchSurface.statePage}>
        <WorkbenchStateSurface
          state="degraded"
          surface="admin-run-monitor"
          title="运行监控暂不可用"
          description={loadError}
          actions={
            <Button type="primary" htmlType="button" className="" onClick={() => void loadRuns(true)}>
              重试
            </Button>
          }
        />
      </div>
    );
  }

  return (
    <div
      data-run-monitor
      data-frontend-governance-state="ready"
      className={`${workbenchSurface.page} run-monitor-page`}
    >
      <PanelHeader
        className="run-monitor-header"
        title="运行监控"
        subtitle="查看任务状态、阶段耗时与故障诊断"
        icon={<Activity size={20} />}
        actions={headerActions}
      />

      {scope.userId ? (
        <div
          data-run-monitor-user-scope={scope.userId}
          className="mx-4 mt-3 flex flex-wrap items-center justify-between gap-2 rounded-md border border-[var(--theme-border)] bg-[var(--theme-info-soft)] px-3 py-2 text-xs text-[var(--theme-text-secondary)]"
        >
          <span>
            当前仅查看用户 <strong className="font-mono text-[var(--theme-text)]">{scope.userId}</strong> 的运行
          </span>
          <a className="font-medium text-[var(--theme-primary)] hover:underline" href="/runs">
            清除用户范围
          </a>
        </div>
      ) : null}

      <div className="run-monitor-toolbar" aria-label="调查范围">
        <Input className="run-monitor-search" value={searchQuery} onChange={(event) => { setSearchQuery(event.target.value); setPage(1); }}
          prefix={<Search size={16} />} aria-label="搜索任务 / Agent / 用户 / 运行编号" placeholder="搜索任务 / Agent / 用户 / 运行编号" />
        <LobeSelectField value={String(hours)} aria-label="统计时间窗" className="w-32"
          options={[{ value: "1", label: "最近 1 小时" }, { value: "24", label: "最近 24 小时" }, { value: "168", label: "最近 7 天" }]}
          onChange={(value) => { invalidateMonitorScope(); setParams((current) => { current.set("hours", value); current.delete("model"); current.delete("page"); return current; }, { replace: true }); }} />
        <LobeSelectField value={modelFilter} aria-label="模型配置筛选" className="w-56"
          options={[{ value: "all", label: "全部模型配置" }, ...(monitorReport?.models ?? []).map((model) => ({ value: `${model.model_id}@${model.model_gateway_revision ?? ""}`, label: `${model.model_value || "未记录模型名称"}${model.model_gateway_revision == null ? "" : ` · 配置 v${model.model_gateway_revision}`}` }))]}
          onChange={(value) => { invalidateMonitorScope(); setModelFilter(value); }} />
        <LobeSelectField value={order} aria-label="运行排序" className="w-36"
          options={[{ value: "slow", label: "耗时从长到短" }, { value: "recent", label: "最近提交优先" }]}
          onChange={(value) => { invalidateMonitorScope(); setOrder(value as "slow" | "recent"); }} />
        {agentFilter ? <Button type="default" htmlType="button" className="text-xs" onClick={() => { invalidateMonitorScope(); setAgentFilter(null); }}>Agent：{agentFilter.label} · 清除</Button> : null}
      </div>
      <div className="run-monitor-overview-container"><RunPerformanceSummary report={monitorReport} /></div>

      <div className="run-monitor-list-toolbar">
        <div
          className="run-monitor-status-tabs"
          role="group"
          aria-label="运行状态筛选"
        >
          {STATUS_FILTERS.map((filter) => (
            <Button type="text"
              key={filter.value}
              htmlType="button"
              aria-pressed={statusFilter === filter.value}
              onClick={() => { if (statusFilter !== filter.value) { invalidateMonitorScope(); setStatusFilter(filter.value); } }}
              className="run-monitor-status-tab"
            >
              {filter.label}{statusCount(filter.value, monitorReport) == null ? "" : ` ${statusCount(filter.value, monitorReport)}`}
            </Button>
          ))}
        </div>
        <p className="text-xs text-[var(--theme-text-tertiary)]" aria-live="polite">
          当前展示 {runs.length} 条 · 搜索后 {filteredRuns.length} 条{monitorReport?.has_more ? " · 更多任务请缩小范围" : ""}
        </p>
      </div>

      {loadError ? (
        <div
          role="status"
          className="mx-4 mb-3 flex items-start gap-2 border-l-2 border-l-[var(--theme-warning)] bg-[var(--theme-warning-soft)] px-3 py-2 text-xs text-[var(--theme-warning)]"
        >
          <AlertTriangle size={15} className="mt-0.5 shrink-0" />
          <span>刷新失败，当前显示上一次成功结果：{loadError}</span>
        </div>
      ) : null}

      <div className="run-monitor-list relative min-h-64 flex-1 px-4 pb-0">
        <section
          ref={listScroll}
          onScroll={(event) => scrollPositions.current.set(location.search, event.currentTarget.scrollTop)}
          aria-label="最近 Worker 请求"
          className="h-full min-w-0 overflow-auto rounded-b-lg border border-t-0 border-[var(--theme-border)] bg-[var(--theme-workbench-panel)]"
        >
          {filteredRuns.length ? (
            <>
              <DesktopRunTable
                runs={visibleRuns}
                selectedRunId={selectedRunId}
                onSelect={selectRun}
              />
              <MobileRunList
                runs={visibleRuns}
                selectedRunId={selectedRunId}
                onSelect={selectRun}
              />
            </>
          ) : (
            <div className="flex min-h-64 flex-col items-center justify-center px-6 text-center">
              {searchQuery || statusFilter !== "all" ? (
                <CircleX size={22} className="text-[var(--theme-text-tertiary)]" />
              ) : (
                <CheckCircle2 size={22} className="text-[var(--theme-success)]" />
              )}
              <h2 className="mt-3 text-sm font-semibold text-[var(--theme-text)]">
                {searchQuery || statusFilter !== "all" ? "没有匹配的运行" : "最近没有 Worker 请求"}
              </h2>
              <p className="mt-1 text-xs text-[var(--theme-text-secondary)]">
                {searchQuery || statusFilter !== "all"
                  ? "调整状态筛选或搜索条件后重试。"
                  : "点击右上角刷新按钮后，新的 Chat 请求会出现在这里。"}
              </p>
            </div>
          )}
        </section>

        {selectedRunId ? <RunQuickView key={selectedRunId} runId={selectedRunId} search={location.search} onClose={closeDetail} /> : null}
      </div>

      {filteredRuns.length ? (
        <nav
          aria-label="运行分页"
          className="run-monitor-pagination"
        >
          <span className="tabular-nums">
            第 {currentPage} / {pageCount} 页 · 显示 {Math.min((currentPage - 1) * PAGE_SIZE + 1, filteredRuns.length)}-
            {Math.min(currentPage * PAGE_SIZE, filteredRuns.length)} / {filteredRuns.length} 条
          </span>
          <div className="flex shrink-0 items-center gap-1">
            <Button type="default"
              htmlType="button"
              className="inline-flex h-8 items-center gap-1 rounded-md px-2.5"
              onClick={() => setPage((current) => Math.max(1, current - 1))}
              disabled={currentPage === 1}
              aria-label="上一页"
            >
              <ChevronLeft size={14} />
              <span>上一页</span>
            </Button>
            {Array.from({ length: pageCount }, (_, index) => index + 1).map((pageNumber) => <Button type="text"
              key={pageNumber} htmlType="button" className="run-monitor-page-number" aria-label={`第 ${pageNumber} 页`}
              aria-current={currentPage === pageNumber ? "page" : undefined} onClick={() => setPage(pageNumber)}
            >{pageNumber}</Button>)}
            <Button type="default"
              htmlType="button"
              className="inline-flex h-8 items-center gap-1 rounded-md px-2.5"
              onClick={() => setPage((current) => Math.min(pageCount, current + 1))}
              disabled={currentPage === pageCount}
              aria-label="下一页"
            >
              <span>下一页</span>
              <ChevronRight size={14} />
            </Button>
          </div>
        </nav>
      ) : null}

      <span className="sr-only" aria-live="polite">
        {isRefreshing ? "正在刷新运行状态" : `运行状态已更新，${lastUpdatedLabel}`}
      </span>
    </div>
  );
}
