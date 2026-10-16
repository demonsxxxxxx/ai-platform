import type { AdminRunLatency, AdminRunMonitorResponse } from "../../services/api/adminRuns";
import { Table } from "@lobehub/ui/base-ui";
import { formatDateTimeShort } from "../../utils/datetime";
import { formatRunDuration, RUN_LATENCY_LABELS } from "./runPerformance";

export function RunPerformanceSummary({ report }: { report: AdminRunMonitorResponse | null }) {
  if (!report) return null;
  const { counts } = report;
  return (
    <section className="rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)]" data-run-performance-summary aria-label="当前范围的运行性能">
      <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-[var(--theme-border)] px-4 py-3">
        <h2 className="text-sm font-semibold text-[var(--theme-text)]">当前范围 · {counts.total} 个任务</h2>
        <span className="text-[11px] text-[var(--theme-text-tertiary)]">{formatDateTimeShort(report.window.from_at)} — {formatDateTimeShort(report.window.to_at)}</span>
      </div>
      <div className="flex flex-wrap gap-x-5 gap-y-2 px-4 pt-3 text-xs text-[var(--theme-text-secondary)]">
        <span>成功 {counts.succeeded}</span>
        <span className={counts.failed ? "text-[var(--theme-danger)]" : ""}>失败 {counts.failed}</span>
        <span>取消 {counts.cancelled}</span>
        <span>执行中 {counts.running} · 排队 {counts.queued}</span>
        <span>成功率 {report.success_rate == null ? "无已完成样本" : `${(report.success_rate * 100).toFixed(1)}%`}<span className="ml-1 text-[var(--theme-text-tertiary)]">（成功 / 成功与失败）</span></span>
      </div>
      <div className="overflow-x-auto px-4 py-3">
        <Table
 size="small" pagination={false}
 dataSource={(Object.keys(RUN_LATENCY_LABELS) as Array<keyof AdminRunLatency>)}
 rowKey={(name) => name}
onRow={(name) => {  return ({"className": "border-t border-[var(--theme-border)]",
"data-latency-metric": name}); }}
 columns={[
{ key: "metric", title: "指标",
render: (_value, name) => (<>
{RUN_LATENCY_LABELS[name]}
</>) },
{ key: "p50", title: "P50",
render: (_value, name) =>  {
const metric = report.latency[name];
return (<>
{formatRunDuration(metric?.p50)}
</>);
} },
{ key: "p95", title: "P95",
render: (_value, name) =>  {
const metric = report.latency[name];
return (<>
{formatRunDuration(metric?.p95)}
</>);
} },
{ key: "p99", title: "P99",
render: (_value, name) =>  {
const metric = report.latency[name];
return (<>
{formatRunDuration(metric?.p99)}
</>);
} },
{ key: "sampleCount", title: "有效样本",
render: (_value, name) =>  {
const metric = report.latency[name];
return (<>
{metric?.sample_count ?? 0}
</>);
} }
]}
/>
      </div>
      <p className="border-t border-[var(--theme-border)] px-4 py-2 text-[11px] leading-5 text-[var(--theme-text-tertiary)]">
        每个任务只计一次；总耗时只统计已结束任务。缺失观测不按 0 计算。浏览器数据来自发起任务的聊天页，不代表屏幕绘制完成。
      </p>
    </section>
  );
}
