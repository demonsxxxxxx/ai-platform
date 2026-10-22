import { useCallback, useMemo, useState, type Key } from "react";
import { Button, Input, Pagination, Table } from "@lobehub/ui/base-ui";
import { Activity, Database, RefreshCw, Users, Zap } from "lucide-react";
import { modelUsageApi, type UsageCounts, type UsageUsersQuery } from "../../services/api/modelUsage";
import { usageDateRange, isUsageDateRangeValid, type UsagePeriod } from "./usageDateRange";
import { useUsageReport } from "./useUsageReport";
import { UsageCoverage, UsageError, UsageEmpty } from "./UsageReportState";
import { UsageModelDetails } from "./UsageModelDetails";

const periods: Array<{ value: UsagePeriod; label: string; days?: number }> = [
  { value: "today", label: "今天", days: 1 },
  { value: "7days", label: "近7天", days: 7 },
  { value: "30days", label: "近30天", days: 30 },
  { value: "custom", label: "自定义" },
];
const PAGE_SIZE = 50;

const formatTokens = (value: number) => value.toLocaleString("zh-CN");

function Metric({ icon: Icon, label, value, hint }: { icon: typeof Activity; label: string; value: string; hint?: string }) {
  return <div className="usage-metric min-w-0 border-r border-[var(--theme-border)] p-4 last:border-r-0 sm:p-5">
    <div className="flex items-center gap-2">
      <span className="usage-metric-icon grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)]"><Icon size={16} aria-hidden="true" /></span>
      <div className="usage-metric-label text-xs text-[var(--theme-text-secondary)]">{label}</div>
    </div>
    <div className="usage-metric-value mt-2 break-all text-[22px] font-semibold tabular-nums tracking-tight sm:text-[26px]">{value}</div>
    {hint && <div className="usage-metric-hint mt-0.5 text-xs text-[var(--theme-text-secondary)]">{hint}</div>}
  </div>;
}

function UsageSummary({ summary, total }: { summary?: UsageCounts | null; total: number }) {
  const unknown = summary === undefined;
  return <section aria-label="用量概览" className="usage-metric-strip grid grid-cols-2 overflow-hidden rounded-xl border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] sm:grid-cols-4">
    <Metric icon={Zap} label="已记录总 Token" value={unknown ? "—" : summary ? formatTokens(summary.total_tokens) : "—"} hint={summary === null ? "尚无已记录数据" : undefined} />
    <Metric icon={Activity} label="输入合计" value={unknown ? "—" : summary ? formatTokens(summary.input_total) : "—"} />
    <Metric icon={Database} label="输出" value={unknown ? "—" : summary ? formatTokens(summary.output_tokens) : "—"} />
    <Metric icon={Users} label="用量用户数" value={formatTokens(total)} hint="当前筛选范围" />
  </section>;
}

function UsageComposition({ summary }: { summary?: UsageCounts | null }) {
  const values = summary ? [
    ["普通输入", summary.input_tokens, "var(--theme-primary)"],
    ["缓存读取", summary.cache_read_input_tokens, "#7c93b8"],
    ["缓存创建", summary.cache_creation_input_tokens, "#b39b72"],
    ["输出", summary.output_tokens, "#6e9c8a"],
  ] as const : [];
  const total = values.reduce((sum, [, value]) => sum + value, 0);
  return <section aria-label="用量构成" className="usage-composition rounded-xl border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-4 sm:p-5">
    <div className="usage-section-heading flex items-start justify-between gap-4"><div><h2 className="m-0 text-base font-semibold">用量构成</h2><p className="mt-1 text-xs text-[var(--theme-text-secondary)]">按实际记录拆分输入、缓存与输出。</p></div><strong className="whitespace-nowrap text-xs tabular-nums">{summary === undefined ? "—" : summary === null ? "尚无已记录数据" : `${formatTokens(total)} Token`}</strong></div>
    {summary ? <>
      <div className="usage-composition-bar mt-4 flex h-3 overflow-hidden rounded-full bg-[var(--theme-bg-sidebar)]" aria-label="用量构成比例">{values.map(([label, value, color]) => <span key={label} title={`${label} ${formatTokens(value)}`} style={{ width: total ? `${value / total * 100}%` : "0%", background: color }} />)}</div>
      <div className="usage-composition-legend mt-3 grid grid-cols-2 gap-x-5 gap-y-3 text-xs text-[var(--theme-text-secondary)] sm:grid-cols-4">{values.map(([label, value, color]) => <span key={label} className="flex min-w-0 flex-col gap-1"><span className="flex min-w-0 items-center gap-1.5"><i className="h-2 w-2 shrink-0 rounded-full" style={{ background: color }} aria-hidden="true" /><span className="truncate">{label}</span></span><span className="flex items-center gap-2 pl-3.5"><b className="tabular-nums text-[var(--theme-text)]">{formatTokens(value)}</b><em className="not-italic tabular-nums">{total ? `${Math.round(value / total * 100)}%` : "0%"}</em></span></span>)}</div>
    </> : <p className="mt-4 text-xs text-[var(--theme-text-secondary)]">{summary === undefined ? "服务端未提供整个日期范围的汇总。" : "尚无已记录数据"}</p>}
  </section>;
}

function UsageUsers({ query, onPage }: { query: UsageUsersQuery; onPage: (page: number) => void }) {
  const load = useCallback((signal: AbortSignal) => modelUsageApi.users(query, signal), [query]);
  const { data, loading, error, retry } = useUsageReport(load);
  const [expanded, setExpanded] = useState<Key[]>([]);
  const dates = useMemo(() => ({ start_date: query.start_date, end_date: query.end_date }), [query.start_date, query.end_date]);
  return <section aria-label="用户用量排名" className="min-w-0 space-y-4">
    {loading && <p role="status">正在加载用量…</p>}
    {error !== undefined && <UsageError error={error} retry={retry} />}
    {data && <>
      <UsageSummary summary={data.summary} total={data.total} />
      <UsageComposition summary={data.summary} />
      <UsageCoverage meta={data.meta} />
      <div className="usage-section-heading flex items-start justify-between gap-4"><div><h2 id="usage-ranking-title" className="m-0 text-base font-semibold">用户排名</h2><p className="mt-1 text-xs text-[var(--theme-text-secondary)]">按已记录总 Token 降序；展开可查看实际模型明细。</p></div></div>
      <div className="min-w-0 overflow-x-auto" role="region" aria-labelledby="usage-ranking-title" tabIndex={0}>
        <Table dataSource={data.data} rowKey={(row) => row.user_id === null ? "unassociated" : `user:${row.user_id}`} pagination={false} tableLayout="fixed" scroll={{ x: 900 }} locale={{ emptyText: <UsageEmpty meta={data.meta} /> }} expandable={{
          columnWidth: 60,
          expandedRowKeys: expanded,
          onExpandedRowsChange: (keys) => setExpanded([...keys]),
          rowExpandable: (row) => row.user_id !== null,
          expandIcon: ({ record, expanded: open, expandable, onExpand }) => expandable ? <Button size="small" aria-label={`${open ? "收起" : "展开"}${record.work_id ?? record.display_name ?? "用户"}模型用量`} aria-expanded={open} onClick={(event) => onExpand(record, event)}>{open ? "−" : "+"}</Button> : null,
          expandedRowRender: (row, _index, _indent, open) => open && row.user_id !== null ? <UsageModelDetails userId={row.user_id} dates={dates} /> : null,
        }} columns={[
          { key: "rank", title: "排名", width: 56, align: "center", render: (_value, _row, index) => query.offset + index + 1 },
          { key: "identity", title: "用户", width: 280, render: (_value, row) => <div><strong className="block">{row.user_id === null ? "未关联用户" : row.display_name ?? "未提供姓名"}</strong><span className="text-xs text-[var(--theme-text-secondary)]">{row.work_id ?? "未提供工号"}</span></div> },
          { key: "input", title: "输入合计", width: 150, align: "right", render: (_value, row) => formatTokens(row.input_total) },
          { key: "output", title: "输出", width: 140, align: "right", render: (_value, row) => formatTokens(row.output_tokens) },
          { key: "total", title: "总量", width: 210, align: "right", render: (_value, row) => { const maxTotal = Math.max(...data.data.map((item) => item.total_tokens), 0); const ratio = maxTotal ? Math.min(100, row.total_tokens / maxTotal * 100) : 0; return <div className="flex items-center justify-end gap-3"><span>{formatTokens(row.total_tokens)}</span><span className="inline-flex h-1.5 w-16 shrink-0 rounded-full bg-[var(--theme-bg-sidebar)]" title="相对当前页首位总量"><span aria-label={`相对当前页首位总量 ${Math.round(ratio)}%`} className="h-full rounded-full bg-[var(--theme-primary)] opacity-60" style={{ width: `${ratio}%` }} /></span></div>; } },
        ]} />
      </div>
      <Pagination current={Math.floor(query.offset / query.limit) + 1} pageSize={query.limit} total={data.total} showSizeChanger={false} onChange={onPage} showTotal={(total) => `共 ${total} 位用户`} />
    </>}
  </section>;
}

export function UsagePage() {
  const [period, setPeriod] = useState<UsagePeriod>("30days");
  const [range, setRange] = useState(() => usageDateRange(30));
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [refreshKey, setRefreshKey] = useState(0);
  const invalidRange = !isUsageDateRangeValid(range);
  const query = useMemo(() => ({ start_date: range.start, end_date: range.end, q: search.trim(), limit: PAGE_SIZE, offset }), [range.start, range.end, search, offset]);

  return (
    <section className="h-full overflow-auto bg-[var(--theme-workbench-canvas)] text-[var(--theme-text)]" aria-labelledby="usage-title">
      <div className="mx-auto flex max-w-[1200px] flex-col gap-6 p-4 sm:p-8">
        <header>
          <h1 id="usage-title" className="text-[26px] font-semibold">用量统计</h1>
          <p className="mt-2 text-sm text-[var(--theme-text-secondary)]">按工号查看实际模型用量，按已记录总 Token 降序排列。</p>
        </header>
        <div className="usage-toolbar flex flex-col gap-3 sm:flex-row sm:items-end">
          <fieldset className="min-w-0 sm:flex-1">
            <legend className="mb-2 text-sm">统计日期（北京时间，含起止日期）</legend>
            <div className="flex flex-wrap gap-2">
              {periods.map(({ value, label, days }) => (
                <Button key={value} type={period === value ? "primary" : "default"} aria-pressed={period === value} onClick={() => {
                  setPeriod(value);
                  setOffset(0);
                  if (days) setRange(usageDateRange(days));
                }}>{label}</Button>
              ))}
            </div>
            {period === "custom" && (
              <div className="mt-3 flex flex-wrap gap-3">
                <div>
                  <label htmlFor="usage-start" className="mb-1 block text-sm">开始日期</label>
                  <Input id="usage-start" type="date" value={range.start} aria-invalid={invalidRange} aria-describedby={invalidRange ? "usage-date-error" : undefined} onChange={(event) => { setRange({ ...range, start: event.target.value }); setOffset(0); }} />
                </div>
                <div>
                  <label htmlFor="usage-end" className="mb-1 block text-sm">结束日期</label>
                  <Input id="usage-end" type="date" value={range.end} aria-invalid={invalidRange} aria-describedby={invalidRange ? "usage-date-error" : undefined} onChange={(event) => { setRange({ ...range, end: event.target.value }); setOffset(0); }} />
                </div>
              </div>
            )}
            {invalidRange ? <p id="usage-date-error" role="alert" className="mt-2 text-sm">请选择有效且完整的日期，结束日期不能早于开始日期。</p> : <p className="mt-2 text-sm text-[var(--theme-text-secondary)]">{range.start} 至 {range.end}</p>}
          </fieldset>
          <div className="usage-search flex min-w-0 items-end gap-2 sm:w-[min(48%,28rem)]">
            <div className="min-w-0 flex-1">
              <label htmlFor="usage-search" className="mb-1 block text-sm">搜索工号或姓名</label>
              <Input id="usage-search" value={search} placeholder="输入工号或姓名" onChange={(event) => { setSearch(event.target.value); setOffset(0); }} />
            </div>
            <Button aria-label="刷新用量" icon={<RefreshCw size={16} aria-hidden="true" />} onClick={() => setRefreshKey((value) => value + 1)}><span className="hidden sm:inline">刷新</span></Button>
          </div>
        </div>
        {!invalidRange && <UsageUsers key={`${JSON.stringify(query)}:${refreshKey}`} query={query} onPage={(page) => setOffset((page - 1) * PAGE_SIZE)} />}
        <details className="text-sm text-[var(--theme-text-secondary)]">
          <summary className="cursor-pointer">统计口径与采集说明</summary>
          <p className="mt-2">按用量记录产生时间归入北京时间自然日。输入合计 = 输入 + 缓存读取 + 缓存创建；总 Token = 输入合计 + 输出。展开用户查看实际模型明细。</p>
          <p className="mt-2">仅统计实际增量记录，不按任务绑定模型估算，不补造历史数据。已记录的 0 与未采集不同；采集状态描述整个筛选窗口，不代表每位用户或模型的完整性。最终报告也不保证覆盖提供方全部消耗。本页不用于费用结算。</p>
        </details>
      </div>
    </section>
  );
}
