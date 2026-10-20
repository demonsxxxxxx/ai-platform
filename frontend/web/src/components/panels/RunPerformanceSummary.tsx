import { CheckCircle2, CircleAlert, LoaderCircle, Play } from "lucide-react";
import { Table } from "@lobehub/ui/base-ui";
import type { AdminRunLatency, AdminRunMonitorResponse } from "../../services/api/adminRuns";
import { formatDateTimeShort } from "../../utils/datetime";
import { formatRunDuration, RUN_LATENCY_LABELS } from "./runPerformance";
import { RunStatusTrend } from "./RunStatusTrend";

export function RunPerformanceSummary({ report }: { report: AdminRunMonitorResponse | null }) {
  if (!report) return null;
  const { counts } = report;
  const metrics = Object.keys(RUN_LATENCY_LABELS) as Array<keyof AdminRunLatency>;
  const tiles = [
    { label: "总任务数", value: counts.total, icon: Play, tone: "success", detail: `排队 ${counts.queued} · 已取消 ${counts.cancelled}` },
    { label: "执行中", value: counts.running, icon: LoaderCircle, tone: "info", detail: "含取消中的任务" },
    { label: "已成功", value: counts.succeeded, icon: CheckCircle2, tone: "success", detail: report.success_rate == null ? "暂无成功率样本" : `成功率 ${(report.success_rate * 100).toFixed(1)}%` },
    { label: "失败", value: counts.failed, icon: CircleAlert, tone: "danger", detail: "打开详情查看诊断" },
  ];
  return (
    <section data-run-performance-summary aria-label="当前范围的运行性能">
      <div className="run-monitor-overview">
        <div className="run-overview-card run-monitor-metrics" aria-label="运行摘要">
          {tiles.map(({ label, value, icon: Icon, tone, detail }) => <div key={label} className="run-monitor-metric" data-run-metric={label}>
            <span className="run-metric-icon" data-tone={tone}><Icon size={19} aria-hidden="true" /></span>
            <p className="mt-4 text-xs text-[var(--theme-text-secondary)]">{label}</p>
            <p className="mt-1.5 text-2xl font-semibold tabular-nums tracking-tight">{value.toLocaleString("zh-CN")}</p>
            <p className="mt-2 text-[11px] text-[var(--theme-text-tertiary)]">{detail}</p>
          </div>)}
        </div>
        <div className="run-overview-card run-latency-summary">
          <h2 className="run-overview-heading">阶段耗时 <span className="font-normal text-[var(--theme-text-tertiary)]">（P50）</span></h2>
          <dl>{metrics.map((name) => <div key={name} className="run-latency-row" data-latency-metric={name}>
            <dt className="text-[var(--theme-text-secondary)]">{RUN_LATENCY_LABELS[name]}</dt>
            <dd className="shrink-0 font-medium tabular-nums" title={`有效样本 ${report.latency[name]?.sample_count ?? 0}`}>{formatRunDuration(report.latency[name]?.p50)}</dd>
          </div>)}</dl>
          <p className="px-4 pb-2 text-[11px] text-[var(--theme-text-tertiary)]">各阶段独立统计，不能相加</p>
        </div>
        <RunStatusTrend report={report} />
      </div>
      <details className="run-performance-details">
        <summary className="cursor-pointer text-xs text-[var(--theme-text-secondary)]">性能分位数与样本数 <span className="ml-2 hidden text-[11px] text-[var(--theme-text-tertiary)] sm:inline">{formatDateTimeShort(report.window.from_at)} — {formatDateTimeShort(report.window.to_at)}</span></summary>
        <div className="overflow-x-auto py-2">
          <Table size="small" pagination={false} dataSource={metrics.map((name) => ({ name, ...report.latency[name] }))} rowKey="name" columns={[
            { key: "metric", title: "指标", render: (_value, metric) => RUN_LATENCY_LABELS[metric.name] },
            ...(["p50", "p95", "p99"] as const).map((key) => ({ key, title: key.toUpperCase(), render: (_value: unknown, metric: { p50?: number | null; p95?: number | null; p99?: number | null }) => formatRunDuration(metric[key]) })),
            { key: "sample_count", title: "有效样本", align: "right", render: (_value, metric) => metric.sample_count ?? 0 },
          ]} />
        </div>
        <p className="text-[11px] leading-5 text-[var(--theme-text-tertiary)]">每个任务只计一次；总耗时只统计已结束任务。缺失观测不按 0 计算。成功率 = 成功 /（成功 + 失败）。浏览器数据来自发起任务的聊天页，不代表屏幕绘制完成。</p>
      </details>
    </section>
  );
}
