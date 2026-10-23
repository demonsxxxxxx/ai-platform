import { Button, Input } from "@lobehub/ui/base-ui";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleX,
  Copy,
  Pause,
  Play,
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { PanelHeader } from "../common/PanelHeader";
import { Table } from "@lobehub/ui/base-ui";
import { PanelLoadingState } from "../common/PanelLoadingState";
import { WorkbenchStateSurface } from "../workbench/WorkbenchStateSurface";
import { workbenchSurface } from "../workbench/workbenchSurface";
import {
  adminRunsApi,
  readAdminRunDeepLinkScope,
  type AdminRunDiagnosticsResponse,
  type AdminRunDetailResponse,
  type AdminRunSummary,
  type AdminRunInvestigation,
  type AdminRunMonitorResponse,
} from "../../services/api/adminRuns";
import { formatDateTimeShort } from "../../utils/datetime";
import { buildAdminRunMonitorView } from "./adminRunTimeline";
import { RunDiagnosticsSection } from "./RunDiagnosticsSection";
import { LobeSelectField } from "../lobe-ui/LobeSelectField";
import { RunPerformanceSummary } from "./RunPerformanceSummary";
import { RunInvestigationTimeline } from "./RunInvestigationTimeline";
import { formatRunDuration } from "./runPerformance";
import { AdminRunTrajectoryReplay } from "./AdminRunTrajectoryReplay";

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

const STATUS_LABELS: Record<string, string> = {
  queued: "排队",
  running: "执行中",
  succeeded: "成功",
  failed: "失败",
  cancelled: "已取消",
  cancel_requested: "取消中",
  denied: "已拒绝",
};

const STATUS_TONES: Record<string, string> = {
  queued:
    "bg-[var(--theme-warning-soft)] text-[var(--theme-warning)] ring-[var(--theme-warning-ring)]",
  running:
    "bg-[var(--theme-info-soft)] text-[var(--theme-info)] ring-[var(--theme-info-ring)]",
  succeeded:
    "bg-[var(--theme-success-soft)] text-[var(--theme-success)] ring-[var(--theme-success-ring)]",
  failed:
    "bg-[var(--theme-danger-soft)] text-[var(--theme-danger-text)] ring-[var(--theme-danger-ring)]",
  cancelled:
    "bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)] ring-[var(--theme-border)]",
  cancel_requested:
    "bg-[var(--theme-warning-soft)] text-[var(--theme-warning)] ring-[var(--theme-warning-ring)]",
  denied:
    "bg-[var(--theme-danger-soft)] text-[var(--theme-danger-text)] ring-[var(--theme-danger-ring)]",
};

function normalized(value: string | null | undefined): string {
  return value?.trim().toLowerCase() ?? "";
}

function displayText(value: string | null | undefined): string {
  return value?.trim() ?? "";
}

function runDisplayTitle(run: AdminRunSummary): string {
  return (
    displayText(run.session_title) ||
    displayText(run.task_summary) ||
    "未命名任务"
  );
}

function userDisplayName(run: AdminRunSummary): string {
  return displayText(run.user_display_name) || "未知用户";
}

function workspaceDisplayName(run: AdminRunSummary): string {
  return displayText(run.workspace_name) || "默认工作区";
}

function agentDisplayName(run: AdminRunSummary): string {
  return displayText(run.agent_name) || "未命名 Agent";
}

function skillDisplayName(run: AdminRunSummary): string {
  return (
    displayText(run.skill_name) ||
    (run.execution_kind === "harness_chat" ? "通用对话" : "未命名 Skill")
  );
}

function compactIdentifier(value: string | null | undefined): string {
  const text = displayText(value);
  if (!text) return "-";
  if (text.length <= 18) return text;
  return `${text.slice(0, 8)}…${text.slice(-6)}`;
}

export function filterAdminRuns(
  runs: AdminRunSummary[],
  status: StatusFilter,
  query: string,
): AdminRunSummary[] {
  const normalizedQuery = normalized(query);
  return runs.filter((run) => {
    if (status !== "all" && run.status !== status && !(status === "running" && run.status === "cancel_requested")) return false;
    if (!normalizedQuery) return true;
    return [
      run.session_title,
      run.task_summary,
      run.user_display_name,
      run.workspace_name,
      run.agent_name,
      run.skill_name,
      run.run_id,
      run.session_id,
      run.user_id,
      run.workspace_id,
      run.agent_id,
      run.skill_id,
      run.trace_id,
      run.error_code,
    ].some((value) => normalized(value).includes(normalizedQuery));
  });
}


function statusCount(filter: StatusFilter, report: AdminRunMonitorResponse | null): number | null {
  if (!report) return null;
  if (filter === "all") return report.counts.total;
  return report.counts[filter] ?? null;
}

function statusLabel(status: string | null | undefined): string {
  return STATUS_LABELS[status ?? ""] ?? status ?? "未知";
}

function statusTone(status: string | null | undefined): string {
  return (
    STATUS_TONES[status ?? ""] ??
    "bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)] ring-[var(--theme-border)]"
  );
}

function dateTime(value: string | null | undefined): string {
  return value ? formatDateTimeShort(value) : "-";
}

function durationLabel(run: AdminRunSummary): string {
  if (run.latency?.total_ms != null) return formatRunDuration(run.latency.total_ms);
  if (run.status === "queued") return "排队中";
  if (run.status === "running") return "执行中";
  return "未采集";
}

function tokenCountLabel(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "未记录";
  return new Intl.NumberFormat("zh-CN").format(value);
}

function estimatedCostLabel(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "未记录";
  return `${new Intl.NumberFormat("zh-CN").format(value)} 最小计价单位`;
}

function byteSizeLabel(value: number): string {
  if (!Number.isFinite(value) || value < 0) return "-";
  if (value < 1_024) return `${value} B`;
  if (value < 1_048_576) return `${(value / 1_024).toFixed(1)} KB`;
  return `${(value / 1_048_576).toFixed(1)} MB`;
}

function StatusBadge({ status }: { status: string | null | undefined }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-2 py-1 text-xs font-medium ring-1 ${statusTone(status)}`}
    >
      <span className="size-1.5 rounded-full bg-current" aria-hidden="true" />
      {statusLabel(status)}
    </span>
  );
}

function IdentityField({ label, value }: { label: string; value?: string | null }) {
  const [copied, setCopied] = useState(false);
  const copyValue = async () => {
    if (!value || !navigator.clipboard) return;
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1_500);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div className="min-w-0">
      <dt className="flex items-center justify-between gap-2 text-[11px] font-medium text-[var(--theme-text-tertiary)]">
        <span>{label}</span>
        {value ? (
          <Button type="text"
            htmlType="button"
            className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] hover:bg-[var(--theme-bg-sidebar)]"
            onClick={() => void copyValue()}
            aria-label={`复制${label}`}
          >
            <Copy size={11} />
            {copied ? "已复制" : "复制"}
          </Button>
        ) : null}
      </dt>
      <dd className="mt-1 break-all font-mono text-xs leading-5 text-[var(--theme-text)]" title={value || undefined}>
        {value || "-"}
      </dd>
    </div>
  );
}

function RuntimeHealth({ detail }: { detail: AdminRunDetailResponse }) {
  const health = detail.runtime_health;
  const state = detail.run.status === "queued" ? "queued"
    : ["succeeded", "failed", "cancelled"].includes(detail.run.status) ? "terminal"
    : health?.state ?? "unknown";
  const labels = {
    queued: "尚未开始执行",
    terminal: "运行已结束",
    healthy: `${health?.heartbeat_source === "executor" ? "Executor" : "Sandbox"} 心跳正常`,
    stale: "执行心跳或运行租约已过期；不等于 Run 已失败",
    unknown: "未知（缺少当前执行证据）",
    awaiting_reconciliation: "执行器终态已留存，等待平台收尾",
  };
  return (
    <section className="mt-3 rounded-md bg-[var(--theme-bg-sidebar)] p-3 text-xs" data-run-runtime-health>
      <h4 className="font-semibold text-[var(--theme-text)]">执行健康（本次观测）</h4>
      <p className="mt-1 text-[var(--theme-text)]">{labels[state]}</p>
      {health?.async_dispatch_accepted && detail.run.status === "running" ? (
        <p className="mt-1 text-[var(--theme-text-secondary)]">
          异步派发已接纳；队列 Worker 心跳仅记录交接前的队列持有状态。
        </p>
      ) : null}
      {health?.queue_last_heartbeat_at ? (
        <p className="mt-1 text-[var(--theme-text-tertiary)]">
          队列 Worker 心跳（历史）：{dateTime(health.queue_last_heartbeat_at)} · 队列租约（历史）：{dateTime(health.queue_lease_expires_at)}
        </p>
      ) : null}
      {health?.heartbeat_at && (state === "healthy" || state === "stale") ? (
        <p className="mt-1 text-[var(--theme-text-tertiary)]">
          {health.heartbeat_source === "executor" ? "Executor" : "Sandbox"} 最近心跳：{dateTime(health.heartbeat_at)}
        </p>
      ) : null}
      {health?.observed_at ? (
        <p className="mt-1 text-[var(--theme-text-tertiary)]">观测时间：{dateTime(health.observed_at)}</p>
      ) : null}
    </section>
  );
}

function focusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(
    container.querySelectorAll<HTMLElement>(
      'button:not([disabled]), summary, [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  ).filter((element) => {
    if (element.closest("[hidden]")) return false;
    let closed = element.closest<HTMLDetailsElement>("details:not([open])");
    while (closed) {
      if (element !== closed.querySelector(":scope > summary")) return false;
      closed = closed.parentElement?.closest<HTMLDetailsElement>("details:not([open])") ?? null;
    }
    return true;
  });
}

function RunDetail({
  detail,
  diagnostics,
  loading,
  diagnosticsLoading,
  error,
  diagnosticsError,
  diagnosticsExporting,
  diagnosticsExportError,
  onRetryDiagnostics,
  onExportDiagnostics,
  investigation,
  investigationLoading,
  investigationError,
  onRetryInvestigation,
  onFocusAgent,
  onClose,
  fallbackFocusRef,
}: {
  detail: AdminRunDetailResponse | null;
  diagnostics: AdminRunDiagnosticsResponse | null;
  loading: boolean;
  diagnosticsLoading: boolean;
  error: string | null;
  diagnosticsError: string | null;
  diagnosticsExporting: boolean;
  diagnosticsExportError: string | null;
  onRetryDiagnostics: () => void;
  onExportDiagnostics: () => void;
  investigation: AdminRunInvestigation | null;
  investigationLoading: boolean;
  investigationError: string | null;
  onRetryInvestigation: () => void;
  onFocusAgent: (agentId: string, label: string) => void;
  onClose: () => void;
  fallbackFocusRef: RefObject<HTMLButtonElement | null>;
}) {
  const detailRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const restoreFocus =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousBodyOverflow = document.body.style.overflow;
    const fallbackFocus = fallbackFocusRef.current;
    let focusCancelled = false;
    document.body.style.overflow = "hidden";
    queueMicrotask(() => {
      if (!focusCancelled) closeRef.current?.focus();
    });

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;

      const focusable = detailRef.current ? focusableElements(detailRef.current) : [];
      if (!focusable.length) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable.at(-1)!;
      if (!detailRef.current?.contains(document.activeElement)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    const handleFocusIn = (event: FocusEvent) => {
      const detailElement = detailRef.current;
      if (!detailElement || detailElement.contains(event.target as Node)) return;
      focusableElements(detailElement)[0]?.focus();
    };

    document.addEventListener("keydown", handleKeyDown);
    document.addEventListener("focusin", handleFocusIn);
    return () => {
      focusCancelled = true;
      document.removeEventListener("keydown", handleKeyDown);
      document.removeEventListener("focusin", handleFocusIn);
      document.body.style.overflow = previousBodyOverflow;
      if (restoreFocus?.isConnected) restoreFocus.focus();
      else fallbackFocus?.focus();
    };
  }, [fallbackFocusRef]);

  const monitorView = detail
    ? buildAdminRunMonitorView(detail.run, detail.events)
    : null;
  const workerExecution = detail?.worker_execution ?? {
    response: monitorView?.modelOutput ?? "",
    messages: [],
    actions: [],
    model: {},
  };
  const artifacts = detail?.artifacts ?? [];

  return (
    <aside
      ref={detailRef}
      data-run-monitor-detail
      role="dialog"
      aria-modal="true"
      aria-labelledby="run-monitor-detail-title"
      className="h-full min-w-0 overflow-y-auto rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] shadow-[0_8px_24px_rgba(18,38,63,0.12)]"
    >
      <div className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-4 py-3">
        <div className="min-w-0">
          <p className="text-xs font-medium text-[var(--theme-text-secondary)]">
            运行详情
          </p>
          <h2 id="run-monitor-detail-title" className="truncate text-sm font-semibold text-[var(--theme-text)]">
            {detail ? runDisplayTitle(detail.run) : "正在读取运行详情"}
          </h2>
          {detail ? (
            <p className="mt-1 truncate text-[11px] text-[var(--theme-text-tertiary)]">
              {agentDisplayName(detail.run)} · 运行 {compactIdentifier(detail.run.run_id)}
            </p>
          ) : null}
        </div>
        <Button type="text"
          ref={closeRef}
          htmlType="button"
          className="flex size-9 shrink-0 items-center justify-center rounded-md"
          onClick={onClose}
          aria-label="关闭运行详情"
          title="关闭运行详情"
          icon={<X size={17} />}
        />
      </div>

      {loading ? (
        <div className="flex min-h-64 items-center justify-center">
          <PanelLoadingState text="正在读取运行详情" />
        </div>
      ) : error ? (
        <div className="p-4">
          <div
            role="alert"
            className="border-l-2 border-l-[var(--theme-danger)] bg-[var(--theme-danger-soft)] px-3 py-2 text-sm text-[var(--theme-danger-text)]"
          >
            {error}
          </div>
        </div>
      ) : detail ? (
        <div className="divide-y divide-[var(--theme-border)]">
          <section className="p-4" data-worker-execution-result>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-xs font-semibold">任务状态</h3>
              <StatusBadge status={detail.run.status} />
            </div>
            <p className="mt-2 text-sm font-medium">{monitorView?.currentAction}</p>
            <p className="mt-1 text-xs text-[var(--theme-text-secondary)]">
              {userDisplayName(detail.run)} · {workspaceDisplayName(detail.run)} · {agentDisplayName(detail.run)} · {skillDisplayName(detail.run)}
            </p>
            <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2 text-xs text-[var(--theme-text-secondary)]">
              <span>提交 {dateTime(detail.run.created_at)}</span>
              <span>Token {tokenCountLabel(detail.run.total_token_count)}</span>
              <span>估算成本 {estimatedCostLabel(detail.run.estimated_cost_minor)}</span>
            </div>
            <RuntimeHealth detail={detail} />
            {detail.run.error_code ? <p className="mt-2 text-xs text-[var(--theme-danger-text)]">终态分类：{detail.run.error_code}（与最早异常分开解释）</p> : null}
            {detail.run.agent_id ? <Button type="default" htmlType="button" className="mt-3 text-xs" onClick={() => onFocusAgent(detail.run.agent_id!, agentDisplayName(detail.run))}>只看这个 Agent 的任务</Button> : null}
          </section>

          <RunInvestigationTimeline
            key={detail.run.run_id}
            investigation={investigation}
            loading={investigationLoading}
            error={investigationError}
            diagnostics={diagnostics}
            onRetry={onRetryInvestigation}
          />
          <RunDiagnosticsSection
            diagnostics={diagnostics}
            loading={diagnosticsLoading}
            error={diagnosticsError}
            exporting={diagnosticsExporting}
            exportError={diagnosticsExportError}
            onRetry={onRetryDiagnostics}
            onExport={onExportDiagnostics}
          />

          <details className="p-4" data-worker-execution-content>
            <summary className="cursor-pointer text-xs font-semibold">交付结果与完整事件记录</summary>
            {workerExecution.answer_projection ? <p data-run-answer-projection className="mt-2 text-[11px] text-[var(--theme-text-tertiary)]">公开回答：{{ available: "已有可验证回答", incomplete: "回答账本尚不完整", invalid: "回答账本未通过校验", unknown: "未采集或证据不足" }[workerExecution.answer_projection.status]}</p> : null}
            {workerExecution.response ? <p data-run-agent-output className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap break-words text-xs leading-5 text-[var(--theme-text-secondary)]">{workerExecution.response}</p> : null}
            {artifacts.length ? <ul className="mt-3 space-y-1" aria-label="已登记产物">{artifacts.map((artifact) => <li key={artifact.artifact_id} className="text-xs">{artifact.label} · {byteSizeLabel(artifact.size_bytes)}</li>)}</ul> : null}
            <div className="-mx-4 mt-3 border-t border-[var(--theme-border)]">
              <AdminRunTrajectoryReplay key={detail.run.run_id} runId={detail.run.run_id} attempts={diagnostics?.attempts ?? []} messages={workerExecution.messages ?? []} />
            </div>
          </details>

          <details className="p-4">
            <summary className="cursor-pointer text-xs font-semibold">技术关联</summary>
            <dl className="mt-3 grid gap-3 sm:grid-cols-2">
              <IdentityField label="Run ID" value={detail.run.run_id} />
              <IdentityField label="Session ID" value={detail.run.session_id} />
              <IdentityField label={detail.run.trace_id_recorded ? "已记录 Trace ID" : "派生关联 ID"} value={detail.run.trace_id} />
            </dl>
            {detail.sandbox_leases.length ? <details className="mt-3 text-xs"><summary className="cursor-pointer">运行环境关联（{detail.sandbox_leases.length}）</summary><ul className="mt-2 space-y-2">{detail.sandbox_leases.map((lease, index) => <li key={lease.lease_id ?? lease.id ?? index} className="text-[var(--theme-text-secondary)]">{lease.provider || "平台沙箱"} · {statusLabel(lease.status)}<span className="ml-2 break-all font-mono text-[10px]">{lease.lease_id ?? lease.id}</span></li>)}</ul></details> : null}
          </details>
        </div>
      ) : null}
    </aside>
  );
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
<Button type="text"
                    htmlType="button"
                    onClick={() => onSelect(run.run_id)}
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
                  </Button>
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
                    aria-label={`打开 ${run.run_id} 详情`}
                    title="打开详情"
>查看</Button>
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
        <Button type="text"
          key={run.run_id}
          htmlType="button"
          onClick={() => onSelect(run.run_id)}
          aria-label={`查看 ${run.run_id}`}
          data-selected={run.run_id === selectedRunId ? "true" : "false"}
          className="block w-full px-3 py-3 text-left transition-colors hover:bg-[var(--theme-bg-sidebar)] data-[selected=true]:bg-[var(--theme-info-soft)]"
        >
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="truncate text-xs font-medium text-[var(--theme-text)]">
                {runDisplayTitle(run)}
              </p>
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
        </Button>
      ))}
    </div>
  );
}

export function RunMonitorPanel() {
  const scope = useMemo(() => readAdminRunDeepLinkScope(), []);
  const [runs, setRuns] = useState<AdminRunSummary[]>([]);
  const [monitorReport, setMonitorReport] = useState<AdminRunMonitorResponse | null>(null);
  const [hours, setHours] = useState(24);
  const [modelFilter, setModelFilter] = useState("all");
  const [agentFilter, setAgentFilter] = useState<{ id: string; label: string } | null>(null);
  const [order, setOrder] = useState<"slow" | "recent">("recent");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(scope.runId);
  const [detail, setDetail] = useState<AdminRunDetailResponse | null>(null);
  const [diagnostics, setDiagnostics] = useState<AdminRunDiagnosticsResponse | null>(null);
  const [investigation, setInvestigation] = useState<AdminRunInvestigation | null>(null);
  const [investigationLoading, setInvestigationLoading] = useState(false);
  const [investigationError, setInvestigationError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [diagnosticsLoading, setDiagnosticsLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [diagnosticsError, setDiagnosticsError] = useState<string | null>(null);
  const [diagnosticsExporting, setDiagnosticsExporting] = useState(false);
  const [diagnosticsExportError, setDiagnosticsExportError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null);
  const [page, setPage] = useState(1);
  const listRequestSequence = useRef(0);
  const listRequestInFlight = useRef<number | null>(null);
  const detailRequestSequence = useRef(0);
  const diagnosticsRequestSequence = useRef(0);
  const investigationRequestSequence = useRef(0);
  const diagnosticsExportSequence = useRef(0);
  const selectedRunIdRef = useRef<string | null>(scope.runId);
  const refreshButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    selectedRunIdRef.current = selectedRunId;
  }, [selectedRunId]);

  const loadDetail = useCallback(async (runId: string, announce = true) => {
    const requestId = ++detailRequestSequence.current;
    if (announce) setDetailLoading(true);
    setDetailError(null);
    try {
      const response = await adminRunsApi.detail(runId);
      if (requestId !== detailRequestSequence.current || selectedRunIdRef.current !== runId) {
        return;
      }
      setDetail(response);
    } catch (error) {
      if (requestId !== detailRequestSequence.current || selectedRunIdRef.current !== runId) {
        return;
      }
      setDetailError(error instanceof Error ? error.message : "运行详情加载失败");
    } finally {
      if (requestId === detailRequestSequence.current) setDetailLoading(false);
    }
  }, []);

  const loadInvestigation = useCallback(async (runId: string, announce = true) => {
    const requestId = ++investigationRequestSequence.current;
    if (announce) setInvestigationLoading(true);
    setInvestigationError(null);
    try {
      const response = await adminRunsApi.investigation(runId);
      if (requestId !== investigationRequestSequence.current || selectedRunIdRef.current !== runId) return;
      setInvestigation(response);
    } catch (error) {
      if (requestId !== investigationRequestSequence.current || selectedRunIdRef.current !== runId) return;
      setInvestigationError(error instanceof Error ? error.message : "运行调查加载失败");
    } finally {
      if (requestId === investigationRequestSequence.current) setInvestigationLoading(false);
    }
  }, []);

  const loadDiagnostics = useCallback(async (runId: string, announce = true) => {
    const requestId = ++diagnosticsRequestSequence.current;
    if (announce) setDiagnosticsLoading(true);
    setDiagnosticsError(null);
    try {
      const response = await adminRunsApi.diagnostics(runId);
      if (
        requestId !== diagnosticsRequestSequence.current ||
        selectedRunIdRef.current !== runId
      ) return;
      setDiagnostics(response);
    } catch (error) {
      if (
        requestId !== diagnosticsRequestSequence.current ||
        selectedRunIdRef.current !== runId
      ) return;
      setDiagnosticsError(error instanceof Error ? error.message : "运行诊断加载失败");
    } finally {
      if (requestId === diagnosticsRequestSequence.current) setDiagnosticsLoading(false);
    }
  }, []);

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
        agentId: agentFilter?.id, modelId,
        modelGatewayRevision: revision ? Number(revision) : undefined,
      });
      if (requestId !== listRequestSequence.current) return;
      setMonitorReport(response);
      setRuns(response.runs);
      setLoadError(null);
      setLastUpdatedAt(new Date());
      const activeRunId = selectedRunIdRef.current;
      if (activeRunId) {
        void loadDetail(activeRunId, false);
        void loadDiagnostics(activeRunId, false);
        void loadInvestigation(activeRunId, false);
      }
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
  }, [loadDetail, loadDiagnostics, loadInvestigation, scope.userId, hours, modelFilter, agentFilter, statusFilter, order]);

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
    diagnosticsExportSequence.current += 1;
    listRequestSequence.current += 1;
    detailRequestSequence.current += 1;
    diagnosticsRequestSequence.current += 1;
    investigationRequestSequence.current += 1;
  }, []);

  const exportDiagnostics = useCallback(async () => {
    const runId = selectedRunIdRef.current;
    if (!runId) return;
    const requestId = ++diagnosticsExportSequence.current;
    setDiagnosticsExporting(true);
    setDiagnosticsExportError(null);
    try {
      const result = await adminRunsApi.exportDiagnostics(runId);
      const objectUrl = URL.createObjectURL(result.blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = result.filename;
      anchor.rel = "noopener";
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
    } catch (error) {
      if (requestId === diagnosticsExportSequence.current) {
        setDiagnosticsExportError(error instanceof Error ? error.message : "诊断包生成失败");
      }
    } finally {
      if (requestId === diagnosticsExportSequence.current) setDiagnosticsExporting(false);
    }
  }, []);

  const selectRun = useCallback((runId: string) => {
    // Navigation retires UI ownership, not an already requested download.
    diagnosticsExportSequence.current += 1;
    setDiagnosticsExporting(false);
    selectedRunIdRef.current = runId;
    setSelectedRunId(runId);
    setDetail(null);
    setDiagnostics(null);
    setInvestigation(null);
    setInvestigationError(null);
    setDiagnosticsExportError(null);
    void loadDetail(runId);
    void loadDiagnostics(runId);
    void loadInvestigation(runId);
  }, [loadDetail, loadDiagnostics, loadInvestigation]);

  const closeDetail = useCallback(() => {
    diagnosticsExportSequence.current += 1;
    detailRequestSequence.current += 1;
    diagnosticsRequestSequence.current += 1;
    investigationRequestSequence.current += 1;
    selectedRunIdRef.current = null;
    setSelectedRunId(null);
    setDetail(null);
    setDiagnostics(null);
    setInvestigation(null);
    setInvestigationError(null);
    setInvestigationLoading(false);
    setDetailError(null);
    setDiagnosticsError(null);
    setDiagnosticsExportError(null);
    setDiagnosticsExporting(false);
    setDetailLoading(false);
    setDiagnosticsLoading(false);
  }, []);

  const invalidateMonitorScope = useCallback(() => {
    listRequestSequence.current += 1;
    closeDetail();
    setMonitorReport(null);
    setRuns([]);
    setIsLoading(true);
    setLoadError(null);
    setPage(1);
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
    setPage(1);
  }, [searchQuery, statusFilter]);
  useEffect(() => {
    setPage((current) => Math.min(current, pageCount));
  }, [pageCount]);

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
        <Input className="run-monitor-search" value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)}
          prefix={<Search size={16} />} aria-label="搜索任务 / Agent / 用户 / 运行编号" placeholder="搜索任务 / Agent / 用户 / 运行编号" />
        <LobeSelectField value={String(hours)} aria-label="统计时间窗" className="w-32"
          options={[{ value: "1", label: "最近 1 小时" }, { value: "24", label: "最近 24 小时" }, { value: "168", label: "最近 7 天" }]}
          onChange={(value) => { invalidateMonitorScope(); setHours(Number(value)); setModelFilter("all"); }} />
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

        {selectedRunId ? (
          <div className="fixed inset-0 z-[300]">
            <Button type="text"
              htmlType="button"
              data-run-monitor-backdrop
              className="absolute inset-0 cursor-default bg-[var(--theme-overlay-strong)]"
              aria-label="关闭运行详情遮罩"
              onClick={closeDetail}
            />
            <div className="absolute inset-y-0 right-0 w-full xl:w-[640px] 2xl:w-[760px] xl:p-2">
              <RunDetail
                detail={detail}
                diagnostics={diagnostics}
                loading={detailLoading}
                diagnosticsLoading={diagnosticsLoading}
                error={detailError}
                diagnosticsError={diagnosticsError}
                diagnosticsExporting={diagnosticsExporting}
                diagnosticsExportError={diagnosticsExportError}
                onRetryDiagnostics={() => void loadDiagnostics(selectedRunId)}
                onExportDiagnostics={() => void exportDiagnostics()}
                investigation={investigation}
                investigationLoading={investigationLoading}
                investigationError={investigationError}
                onRetryInvestigation={() => void loadInvestigation(selectedRunId)}
                onFocusAgent={(id, label) => { invalidateMonitorScope(); setAgentFilter({ id, label }); }}
                onClose={closeDetail}
                fallbackFocusRef={refreshButtonRef}
              />
            </div>
          </div>
        ) : null}
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
