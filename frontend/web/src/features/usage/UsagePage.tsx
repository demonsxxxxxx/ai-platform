import { useCallback, useLayoutEffect, useMemo, useRef, useState, type Key } from "react";
import { Button, Input, Pagination, Table } from "@lobehub/ui/base-ui";
import { Activity, Database, RefreshCw, Search, Users, Zap } from "lucide-react";
import { modelUsageApi, type UsageCounts, type UsageUsersQuery } from "../../services/api/modelUsage";
import { usageDateRange, isUsageDateRangeValid, type UsagePeriod } from "./usageDateRange";
import { useUsageReport } from "./useUsageReport";
import { UsageError, UsageEmpty } from "./UsageReportState";
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
  return <div className="usage-metric">
    <div className="flex items-center gap-2 text-xs text-[var(--theme-text-secondary)]">
      <span className="grid h-6 w-6 shrink-0 place-items-center rounded-md bg-[var(--theme-bg-sidebar)]"><Icon size={15} aria-hidden="true" /></span>
      <span>{label}</span>
    </div>
    <div className="usage-metric-value">{value}</div>
    {hint && <div className="text-xs text-[var(--theme-text-secondary)]">{hint}</div>}
  </div>;
}

function UsageSummary({ summary, total }: { summary?: UsageCounts | null; total?: number }) {
  return <section aria-label="用量概览" className="usage-metric-strip">
    <Metric icon={Zap} label="已记录总 Token" value={summary ? formatTokens(summary.total_tokens) : "—"} hint={summary === null ? "尚无已记录数据" : undefined} />
    <Metric icon={Activity} label="输入合计" value={summary ? formatTokens(summary.input_total) : "—"} />
    <Metric icon={Database} label="输出" value={summary ? formatTokens(summary.output_tokens) : "—"} />
    <Metric icon={Users} label="用量用户数" value={total === undefined ? "—" : formatTokens(total)} />
  </section>;
}

function UsageComposition({ summary, loading }: { summary?: UsageCounts | null; loading: boolean }) {
  const values = summary ? [
    ["普通输入", summary.input_tokens, "var(--theme-primary)"],
    ["缓存读取", summary.cache_read_input_tokens, "#7c93b8"],
    ["缓存创建", summary.cache_creation_input_tokens, "#b39b72"],
    ["输出", summary.output_tokens, "#6e9c8a"],
  ] as const : [];
  const total = values.reduce((sum, [, value]) => sum + value, 0);
  return <section aria-label="用量构成" className="usage-composition">
    <h2 className="m-0 text-sm font-semibold">用量构成</h2>
    {summary ? <>
      <div className="usage-composition-bar" aria-label="用量构成比例">{values.map(([label, value, color]) => <span key={label} title={`${label} ${formatTokens(value)}`} style={{ width: total ? `${value / total * 100}%` : "0%", background: color }} />)}</div>
      <div className="usage-composition-legend">{values.map(([label, value, color]) => <span key={label} className="flex items-center gap-2 whitespace-nowrap">
        <i className="h-2 w-2 shrink-0 rounded-full" style={{ background: color }} aria-hidden="true" />
        <span>{label}</span><b className="font-medium tabular-nums text-[var(--theme-text)]">{formatTokens(value)}</b><span className="tabular-nums">{total ? `${Math.round(value / total * 100)}%` : "0%"}</span>
      </span>)}</div>
    </> : <p className="my-2 text-xs text-[var(--theme-text-secondary)]">{loading ? "—" : summary === undefined ? "服务端未提供整个日期范围的汇总。" : "尚无已记录数据"}</p>}
  </section>;
}

function UsageUsers({ query, onPage }: { query: UsageUsersQuery; onPage: (page: number) => void }) {
  const load = useCallback((signal: AbortSignal) => modelUsageApi.users(query, signal), [query]);
  const { data, loading, error, retry } = useUsageReport(load);
  const [expanded, setExpanded] = useState<Key[]>([]);
  const dates = useMemo(() => ({ start_date: query.start_date, end_date: query.end_date }), [query.start_date, query.end_date]);
  const tableViewport = useRef<HTMLDivElement>(null);
  const [bodyHeight, setBodyHeight] = useState(160);
  // rc-table cannot measure a fixed body's scrollbar when it has no records.
  const hasRows = !!data?.data.length;
  useLayoutEffect(() => {
    const viewport = tableViewport.current!;
    const header = viewport.querySelector<HTMLElement>(".usage-table-header")!;
    // Measure the available table space and actual header, including responsive toolbar changes.
    const measure = () => setBodyHeight(Math.max(96, viewport.clientHeight - header.getBoundingClientRect().height));
    const observer = new ResizeObserver(measure);
    observer.observe(viewport);
    observer.observe(header);
    measure();
    return () => observer.disconnect();
  }, [hasRows]);
  const first = data?.data.length ? query.offset + 1 : 0;
  const last = data?.data.length ? Math.min(query.offset + data.data.length, data.total) : 0;

  return <section aria-label="用户用量排名" className="usage-report" aria-busy={loading}>
    <UsageSummary summary={data?.summary} total={data?.total} />
    <UsageComposition summary={data?.summary} loading={loading} />
    <div className="usage-ranking">
      <div className="usage-ranking-heading"><h2 id="usage-ranking-title" className="m-0 text-base font-semibold">用户排名</h2><span className="text-xs text-[var(--theme-text-secondary)]">{data ? `共 ${data.total} 位用户` : "—"}</span></div>
      <div ref={tableViewport} className="usage-table-viewport" role="region" aria-labelledby="usage-ranking-title" tabIndex={0}>
        <Table dataSource={data?.data ?? []} rowKey={(row) => row.user_id === null ? "unassociated" : `user:${row.user_id}`} pagination={false} size="small" tableLayout="fixed" scroll={{ x: "max(900px, 100%)", y: hasRows ? bodyHeight : undefined }}
          classNames={{ header: { wrapper: "usage-table-header" }, body: { cell: "usage-user-cell" } }}
          styles={{ header: { cell: { padding: "8px 12px", whiteSpace: "nowrap" } }, body: { cell: { padding: "5px 12px", height: 48, fontVariantNumeric: "tabular-nums" } } }}
          locale={{ emptyText: loading ? <p role="status">正在加载用量…</p> : error !== undefined ? <UsageError error={error} retry={retry} /> : data ? <UsageEmpty meta={data.meta} /> : null }} expandable={{
            columnWidth: 48,
            expandedRowKeys: expanded,
            onExpandedRowsChange: (keys) => setExpanded([...keys]),
            rowExpandable: (row) => row.user_id !== null,
            expandIcon: ({ record, expanded: open, expandable, onExpand }) => expandable ? <Button size="small" style={{ width: 28, height: 28, padding: 0 }} aria-label={`${open ? "收起" : "展开"}${record.work_id ?? record.display_name ?? "用户"}模型用量`} aria-expanded={open} onClick={(event) => onExpand(record, event)}>{open ? "−" : "+"}</Button> : null,
            expandedRowRender: (row, _index, _indent, open) => open && row.user_id !== null ? <UsageModelDetails userId={row.user_id} dates={dates} /> : null,
          }} columns={[
            { key: "rank", title: "排名", width: 64, align: "center", render: (_value, _row, index) => query.offset + index + 1 },
            { key: "identity", title: "用户", render: (_value, row) => <div className="leading-[18px]"><strong className="block truncate font-medium" title={row.display_name ?? undefined}>{row.user_id === null ? "未关联用户" : row.display_name ?? "未提供姓名"}</strong><span className="block truncate text-xs text-[var(--theme-text-secondary)]" title={row.work_id ?? undefined}>{row.work_id ?? "未提供工号"}</span></div> },
            { key: "input", title: "输入合计", width: 170, align: "right", render: (_value, row) => formatTokens(row.input_total) },
            { key: "output", title: "输出", width: 150, align: "right", render: (_value, row) => formatTokens(row.output_tokens) },
            { key: "total", title: "总量", width: 190, align: "right", render: (_value, row) => <strong className="font-medium">{formatTokens(row.total_tokens)}</strong> },
          ]} />
      </div>
      <footer aria-label="用量表格分页" className="usage-table-footer" tabIndex={-1}>
        <span className="whitespace-nowrap text-xs text-[var(--theme-text-secondary)]">{data ? `第${first}–${last}条，共${data.total}条` : "—"}</span>
        <div className="usage-page-controls"><Pagination current={Math.floor(query.offset / query.limit) + 1} pageSize={query.limit} total={data?.total ?? 0} disabled={!data} showSizeChanger={false} showLessItems onChange={onPage} /><span className="whitespace-nowrap text-xs text-[var(--theme-text-secondary)]">50条/页</span></div>
      </footer>
    </div>
  </section>;
}

export function UsagePage() {
  const [period, setPeriod] = useState<UsagePeriod>("30days");
  const [range, setRange] = useState(() => usageDateRange(30));
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [refreshKey, setRefreshKey] = useState(0);
  const pageRoot = useRef<HTMLElement>(null);
  const focusPagination = useRef(false);
  useLayoutEffect(() => {
    if (focusPagination.current) {
      pageRoot.current?.querySelector<HTMLElement>('[aria-label="用量表格分页"]')?.focus();
      focusPagination.current = false;
    }
  }, [offset]);
  const invalidRange = !isUsageDateRangeValid(range);
  const query = useMemo(() => ({ start_date: range.start, end_date: range.end, q: search.trim(), limit: PAGE_SIZE, offset }), [range.start, range.end, search, offset]);

  return <section ref={pageRoot} className="usage-page bg-[var(--theme-workbench-canvas)] text-[var(--theme-text)]" aria-labelledby="usage-title">
    <div className="usage-layout w-full">
      <header><h1 id="usage-title" className="m-0 text-[26px] font-semibold">用量统计</h1></header>
      <div className="usage-toolbar">
        <fieldset className="usage-dates">
          <legend className="sr-only">统计日期（北京时间，含起止日期）</legend>
          <div className="usage-date-presets"><span className="usage-date-label text-sm">统计日期</span>{periods.map(({ value, label, days }) => <Button key={value} size="small" style={{ height: 34, paddingInline: 12 }} type={period === value ? "primary" : "default"} aria-pressed={period === value} onClick={() => {
            setPeriod(value);
            setOffset(0);
            if (days) setRange(usageDateRange(days));
          }}>{label}</Button>)}</div>
          {period === "custom" ? <div className="usage-custom-dates">
            <label htmlFor="usage-start" className="sr-only">开始日期</label>
            <Input id="usage-start" type="date" value={range.start} aria-invalid={invalidRange} aria-describedby={invalidRange ? "usage-date-error" : undefined} onChange={(event) => { setRange({ ...range, start: event.target.value }); setOffset(0); }} />
            <span className="text-xs text-[var(--theme-text-secondary)]">至</span>
            <label htmlFor="usage-end" className="sr-only">结束日期</label>
            <Input id="usage-end" type="date" value={range.end} aria-invalid={invalidRange} aria-describedby={invalidRange ? "usage-date-error" : undefined} onChange={(event) => { setRange({ ...range, end: event.target.value }); setOffset(0); }} />
            <span className="usage-timezone">北京时间</span>
          </div> : <span className="usage-date-range">{range.start} — {range.end}（北京时间）</span>}
        </fieldset>
        <div className="usage-search">
          <label htmlFor="usage-search" className="sr-only">搜索工号或姓名</label>
          <Input id="usage-search" prefix={<Search size={16} aria-hidden="true" />} value={search} placeholder="搜索工号或姓名" onChange={(event) => { setSearch(event.target.value); setOffset(0); }} />
          <Button aria-label="刷新用量" style={{ height: 36, width: 36, padding: 0, flexShrink: 0 }} icon={<RefreshCw size={16} aria-hidden="true" />} onClick={() => setRefreshKey((value) => value + 1)} />
        </div>
        {invalidRange && <p id="usage-date-error" role="alert" className="usage-date-error">请选择有效且完整的日期，结束日期不能早于开始日期。</p>}
      </div>
      {!invalidRange && <UsageUsers key={`${JSON.stringify(query)}:${refreshKey}`} query={query} onPage={(page) => {
        const nextOffset = (page - 1) * PAGE_SIZE;
        if (nextOffset === offset) return;
        focusPagination.current = !!document.activeElement?.closest('[aria-label="用量表格分页"]');
        setOffset(nextOffset);
      }} />}
    </div>
  </section>;
}
