import type { AdminRunLatency, AdminRunMonitorResponse } from "../../services/api/adminRuns";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../common/Table";
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
        <Table className="w-full text-left text-xs">
          <TableHeader><TableRow className="text-[var(--theme-text-tertiary)]">
            <TableHead className="py-2 font-normal">指标</TableHead>
            <TableHead className="py-2 font-normal">P50</TableHead>
            <TableHead className="py-2 font-normal">P95</TableHead>
            <TableHead className="py-2 font-normal">P99</TableHead>
            <TableHead className="py-2 text-right font-normal">有效样本</TableHead>
          </TableRow></TableHeader>
          <TableBody>{(Object.keys(RUN_LATENCY_LABELS) as Array<keyof AdminRunLatency>).map((name) => {
            const metric = report.latency[name];
            return <TableRow key={name} className="border-t border-[var(--theme-border)]" data-latency-metric={name}>
              <TableCell className="py-2 pr-3 text-[var(--theme-text-secondary)]">{RUN_LATENCY_LABELS[name]}</TableCell>
              <TableCell className="py-2 tabular-nums">{formatRunDuration(metric?.p50)}</TableCell>
              <TableCell className="py-2 tabular-nums">{formatRunDuration(metric?.p95)}</TableCell>
              <TableCell className="py-2 tabular-nums">{formatRunDuration(metric?.p99)}</TableCell>
              <TableCell className="py-2 text-right tabular-nums text-[var(--theme-text-secondary)]">{metric?.sample_count ?? 0}</TableCell>
            </TableRow>;
          })}</TableBody>
        </Table>
      </div>
      <p className="border-t border-[var(--theme-border)] px-4 py-2 text-[11px] leading-5 text-[var(--theme-text-tertiary)]">
        每个任务只计一次；总耗时只统计已结束任务。缺失观测不按 0 计算。浏览器数据来自发起任务的聊天页，不代表屏幕绘制完成。
      </p>
    </section>
  );
}
