import { useCallback, useMemo, useState, type Key } from "react";
import { Button, Input, Pagination, Table } from "@lobehub/ui/base-ui";
import { modelUsageApi, type UsageUsersQuery } from "../../services/api/modelUsage";
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

function UsageUsers({ query, onPage }: { query: UsageUsersQuery; onPage: (page: number) => void }) {
  const load = useCallback((signal: AbortSignal) => modelUsageApi.users(query, signal), [query]);
  const { data, loading, error, retry } = useUsageReport(load);
  const [expanded, setExpanded] = useState<Key[]>([]);
  const dates = useMemo(() => ({ start_date: query.start_date, end_date: query.end_date }), [query.start_date, query.end_date]);
  return <section aria-label="用户用量排名" className="min-w-0 space-y-4">
    {loading && <p role="status">正在加载用量…</p>}
    {error !== undefined && <UsageError error={error} retry={retry} />}
    {data && <>
      <UsageCoverage meta={data.meta} />
      <Table dataSource={data.data} rowKey={(row) => row.user_id === null ? "unassociated" : `user:${row.user_id}`} pagination={false} tableLayout="fixed" scroll={{ x: 700 }} locale={{ emptyText: <UsageEmpty meta={data.meta} /> }} expandable={{
        columnWidth: 60,
        expandedRowKeys: expanded,
        onExpandedRowsChange: (keys) => setExpanded([...keys]),
        rowExpandable: (row) => row.user_id !== null,
        expandIcon: ({ record, expanded: open, expandable, onExpand }) => expandable ? <Button size="small" aria-label={`${open ? "收起" : "展开"}${record.work_id ?? record.display_name ?? "用户"}模型用量`} aria-expanded={open} onClick={(event) => onExpand(record, event)}>{open ? "−" : "+"}</Button> : null,
        expandedRowRender: (row, _index, _indent, open) => open && row.user_id !== null ? <UsageModelDetails userId={row.user_id} dates={dates} /> : null,
      }} columns={[
        { key: "employee", title: "工号", width: 180, render: (_value, row) => row.work_id ?? "未提供工号" },
        { key: "name", title: "用户", width: 220, render: (_value, row) => row.user_id === null ? "未关联用户" : row.display_name ?? "未提供姓名" },
        { key: "total", title: "已记录总 Token ↓", width: 220, align: "right", render: (_value, row) => row.total_tokens.toLocaleString("zh-CN") },
      ]} />
      <Pagination current={Math.floor(query.offset / query.limit) + 1} pageSize={query.limit} total={data.total} showSizeChanger={false} onChange={onPage} showTotal={(total) => `共 ${total} 位用户`} />
    </>}
  </section>;
}

export function UsagePage() {
  const [period, setPeriod] = useState<UsagePeriod>("30days");
  const [range, setRange] = useState(() => usageDateRange(30));
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const invalidRange = !isUsageDateRangeValid(range);
  const query = useMemo(() => ({ start_date: range.start, end_date: range.end, q: search.trim(), limit: PAGE_SIZE, offset }), [range.start, range.end, search, offset]);

  return (
    <section className="h-full overflow-auto bg-[var(--theme-workbench-canvas)] text-[var(--theme-text)]" aria-labelledby="usage-title">
      <div className="mx-auto flex max-w-[1200px] flex-col gap-6 p-4 sm:p-8">
        <header>
          <h1 id="usage-title" className="text-[26px] font-semibold">用量统计</h1>
          <p className="mt-2 text-sm text-[var(--theme-text-secondary)]">按工号查看实际模型用量，按已记录总 Token 降序排列。</p>
        </header>
        <fieldset className="min-w-0">
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
        <div className="max-w-md">
          <label htmlFor="usage-search" className="mb-1 block text-sm">搜索工号或姓名</label>
          <Input id="usage-search" value={search} placeholder="输入工号或姓名" onChange={(event) => { setSearch(event.target.value); setOffset(0); }} />
        </div>
        {!invalidRange && <UsageUsers key={JSON.stringify(query)} query={query} onPage={(page) => setOffset((page - 1) * PAGE_SIZE)} />}
        <details className="text-sm text-[var(--theme-text-secondary)]">
          <summary className="cursor-pointer">统计口径与采集说明</summary>
          <p className="mt-2">按用量记录产生时间归入北京时间自然日。输入合计 = 输入 + 缓存读取 + 缓存创建；总 Token = 输入合计 + 输出。展开用户查看实际模型明细。</p>
          <p className="mt-2">仅统计实际增量记录，不按任务绑定模型估算，不补造历史数据。已记录的 0 与未采集不同；采集状态描述整个筛选窗口，不代表每位用户或模型的完整性。最终报告也不保证覆盖提供方全部消耗。本页不用于费用结算。</p>
        </details>
      </div>
    </section>
  );
}
