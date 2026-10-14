import { useState } from "react";
import type {
  AdminRunDiagnosticsResponse, AdminRunInvestigation, AdminRunLatency,
} from "../../services/api/adminRuns";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../common/Table";
import { indexToolDiagnosticEvidence } from "./adminRunTimeline";
import { formatRunDuration, RUN_LATENCY_LABELS } from "./runPerformance";

const SOURCE_LABELS = {
  run_lifecycle: "任务生命周期",
  platform_ledger: "公开文本入库",
  run_attempt: "执行尝试",
  sdk_reported: "SDK 整体报告（含工具等待）",
  sdk_tool_hooks: "工具调用钩子",
};
const STATE_LABELS: Record<string, string> = {
  succeeded: "成功", failed: "失败", denied: "拒绝", cancelled: "取消",
  running: "进行中", waiting: "等待中", recorded: "已记录", unknown: "未知",
};

export function RunInvestigationTimeline({
  investigation, loading, error, diagnostics, onRetry, onSelectRun,
}: {
  investigation: AdminRunInvestigation | null;
  loading: boolean;
  error: string | null;
  diagnostics: AdminRunDiagnosticsResponse | null;
  onRetry: () => void;
  onSelectRun: (runId: string) => void;
}) {
  const [selectedStageId, setSelectedStageId] = useState<string | null>(null);
  if (!investigation) return (
    <section className="p-4" data-run-investigation>
      <h3 className="text-xs font-semibold text-[var(--theme-text)]">阶段调查</h3>
      {error ? <p role="alert" className="mt-2 text-xs text-[var(--theme-danger)]">{error}</p> : <p className="mt-2 text-xs text-[var(--theme-text-secondary)]">{loading ? "正在读取调查证据" : "调查证据尚未读取"}</p>}
      {error ? <button type="button" className="btn-secondary mt-2 text-xs" onClick={onRetry}>重试调查查询</button> : null}
    </section>
  );
  const total = Math.max(investigation.total_ms ?? investigation.elapsed_ms ?? 0, 1);
  const selected = investigation.stages.find((stage) => stage.stage_id === selectedStageId);
  const toolEvidence = indexToolDiagnosticEvidence(
    (diagnostics?.details.observations ?? []).filter((observation) =>
      selected?.attempt_id != null && observation.attempt_id === selected.attempt_id,
    ).flatMap((observation) => [
      ...observation.tool_lifecycles, ...observation.tool_calls, ...observation.tool_policy_denials,
    ]),
  );
  const evidence = selected?.operation_id ? toolEvidence.get(selected.operation_id) : null;
  const baseline = investigation.comparison;
  return (
    <section className="p-4" data-run-investigation>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-xs font-semibold text-[var(--theme-text)]">阶段调查</h3>
        <span className="text-[11px] text-[var(--theme-text-tertiary)]">点击阶段，关联调用与诊断证据</span>
      </div>
      {error ? <p role="alert" className="mt-2 text-xs text-[var(--theme-danger)]">刷新失败，仍显示上次调查：{error}</p> : null}
      <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
        {(Object.keys(RUN_LATENCY_LABELS) as Array<keyof AdminRunLatency>).map((name) => (
          <div className="rounded-md bg-[var(--theme-bg-sidebar)] p-2" key={name} data-run-latency={name}>
            <p className="text-[var(--theme-text-tertiary)]">{RUN_LATENCY_LABELS[name]}</p>
            <p className="mt-1 font-medium tabular-nums">{formatRunDuration(investigation[name])}</p>
          </div>
        ))}
      </div>
      <p className="mt-2 text-[11px] leading-5 text-[var(--theme-text-tertiary)]">
        时间线按平台收到并保存的时间定位。SDK 和工具另列实际报告耗时；重叠区间不能相加，最长区间也不等于已证明的根因。模型供应商内部、上下文构建与网络传输的细分耗时尚未采集。
      </p>
      {investigation.events_truncated || investigation.unbound_event_count > 0 ? (
        <p className="mt-2 text-[11px] text-[var(--theme-warning)]" data-investigation-incomplete>
          {investigation.events_truncated ? "阶段记录只展示最近 500 条。" : ""}
          {investigation.unbound_event_count > 0 ? ` ${investigation.unbound_event_count} 条记录无法绑定已知执行尝试，未并入阶段。` : ""}
        </p>
      ) : null}
      <ol className="mt-3 space-y-1" data-run-stage-timeline>
        {investigation.stages.map((stage) => {
          const left = stage.offset_ms == null ? null : Math.min(100, Math.max(0, stage.offset_ms / total * 100));
          const width = left == null ? 0 : Math.min(100 - left, Math.max(1, (stage.receipt_duration_ms ?? 0) / total * 100));
          return <li key={stage.stage_id}>
            <button type="button" onClick={() => setSelectedStageId(stage.stage_id)} aria-pressed={selectedStageId === stage.stage_id}
              aria-label={`调查阶段 ${stage.label}`} data-stage-id={stage.stage_id}
              className="w-full rounded-md border border-[var(--theme-border)] p-2 text-left transition-colors hover:bg-[var(--theme-bg-sidebar)] aria-pressed:bg-[var(--theme-info-soft)]">
              <div className="flex items-center justify-between gap-2 text-xs">
                <span className="min-w-0 truncate font-medium">{stage.label}</span>
                <span className={stage.status === "failed" || stage.status === "denied" ? "text-[var(--theme-danger)]" : "text-[var(--theme-text-secondary)]"}>
                  {STATE_LABELS[stage.status] ?? stage.status} · {formatRunDuration(stage.duration_ms)}
                </span>
              </div>
              <div className="mt-1.5 h-2 overflow-hidden rounded bg-[var(--theme-bg-sidebar)]" aria-hidden="true">
                {left == null ? <div className="h-full w-full border-b border-dashed border-[var(--theme-border)]" /> : <div className="relative h-full rounded bg-[var(--theme-info)] opacity-60" style={{ left: `${left}%`, width: `${width}%` }} />}
              </div>
              <p className="mt-1 text-[10px] text-[var(--theme-text-tertiary)]">{SOURCE_LABELS[stage.source]}{stage.offset_ms == null ? " · 起点未采集" : ` · 自提交 ${formatRunDuration(stage.offset_ms)}`}{stage.source === "sdk_tool_hooks" && !stage.attempt_id ? " · 尝试身份未知" : ""}</p>
            </button>
          </li>;
        })}
      </ol>
      {selected ? (
        <div className="mt-3 rounded-md border border-[var(--theme-info-ring)] bg-[var(--theme-info-soft)] p-3 text-xs" data-selected-stage-evidence>
          <p className="font-semibold">{selected.label} · 关联证据</p>
          <p className="mt-1 leading-5 text-[var(--theme-text-secondary)]">
            {evidence ? [evidence.last_stage, evidence.state, evidence.reason].filter(Boolean).join(" · ") || "已找到同一工具调用的诊断记录" : selected.operation_id ? "没有该调用的私有诊断；不表示调用未发生。" : "阶段事实见时间线；异常因果链与证据缺口见下方诊断。"}
          </p>
          <p className="mt-1 leading-5 text-[var(--theme-text-secondary)]">
            平台回执跨度：{formatRunDuration(selected.receipt_duration_ms)}
            {selected.source === "sdk_tool_hooks" || selected.source === "sdk_reported" ? ` · 来源报告耗时：${formatRunDuration(selected.duration_ms)}` : ""}
          </p>
          <details className="mt-2 text-[11px] text-[var(--theme-text-tertiary)]">
            <summary className="cursor-pointer">技术关联</summary>
            <p className="mt-1 break-all">平台记录起点：{selected.started_at ?? "未采集"} · 终点：{selected.finished_at ?? "未采集"}</p>
            {[['Attempt', selected.attempt_id], ['调用', selected.operation_id], ['事件', selected.event_id], ['上游事件', selected.causation_event_id]].map(([label, value]) => value ? <p key={label} className="mt-1 break-all">{label}：{value}</p> : null)}
          </details>
        </div>
      ) : null}
      <div className="mt-4 border-t border-[var(--theme-border)] pt-3" data-run-comparison>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h4 className="text-xs font-semibold">同类成功任务对照</h4>
          {baseline.normal_run_id ? <button type="button" className="btn-secondary text-xs" onClick={() => onSelectRun(baseline.normal_run_id!)}>调查正常对照任务</button> : null}
        </div>
        <p className="mt-1 text-[11px] leading-5 text-[var(--theme-text-tertiary)]">
          同 Agent 版本、冻结的 Skill 版本与依赖集合、模型配置；使用相同统计窗口，排除当前任务。任务复杂度仍可能不同。
        </p>
        {baseline.missing_reason ? <p className="mt-2 text-xs text-[var(--theme-text-secondary)]">{baseline.missing_reason === "model_identity_unknown" ? "该任务没有完整模型配置快照，无法建立可比基线。" : "当前窗口没有同类成功任务，不能推断正常耗时。"}</p> : (
          <div className="mt-2 overflow-x-auto">
            <Table className="w-full text-left text-xs">
              <TableHeader><TableRow><TableHead className="py-2 font-normal">指标</TableHead><TableHead className="py-2 font-normal">本次</TableHead><TableHead className="py-2 font-normal">同类 P50</TableHead><TableHead className="py-2 font-normal">同类 P95</TableHead><TableHead className="py-2 text-right font-normal">样本</TableHead></TableRow></TableHeader>
              <TableBody>{(Object.keys(RUN_LATENCY_LABELS) as Array<keyof AdminRunLatency>).map((name) => {
                const metric = baseline.latency[name];
                return <TableRow key={name} className="border-t border-[var(--theme-border)]">
                  <TableCell className="py-2 pr-2 text-[var(--theme-text-secondary)]">{RUN_LATENCY_LABELS[name]}</TableCell>
                  <TableCell className={`py-2 tabular-nums ${investigation[name] != null && metric?.p95 != null && investigation[name]! > metric.p95 ? "text-[var(--theme-warning)]" : ""}`}>{formatRunDuration(investigation[name])}</TableCell>
                  <TableCell className="py-2 tabular-nums">{formatRunDuration(metric?.p50)}</TableCell>
                  <TableCell className="py-2 tabular-nums">{formatRunDuration(metric?.p95)}</TableCell>
                  <TableCell className="py-2 text-right tabular-nums">{metric?.sample_count ?? 0}</TableCell>
                </TableRow>;
              })}</TableBody>
            </Table>
          </div>
        )}
      </div>
    </section>
  );
}
