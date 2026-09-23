import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleX,
  Copy,
  Clock3,
  Pause,
  Play,
  RadioTower,
  RefreshCw,
  ServerCog,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { PanelHeader } from "../common/PanelHeader";
import { PanelLoadingState } from "../common/PanelLoadingState";
import { WorkbenchStateSurface } from "../workbench/WorkbenchStateSurface";
import { workbenchSurface } from "../workbench/workbenchSurface";
import {
  adminRunsApi,
  readAdminRunDeepLinkScope,
  type AdminQueueInsight,
  type AdminRunDiagnosticsResponse,
  type AdminRunDetailResponse,
  type AdminRunSummary,
  type AdminWorkerExecution,
} from "../../services/api/adminRuns";
import { formatDateTimeShort } from "../../utils/datetime";
import {
  buildAdminRunMonitorView,
  type AdminRunEventDiagnostic,
  type AdminRunTimelineItem,
} from "./adminRunTimeline";
import { RunDiagnosticsSection } from "./RunDiagnosticsSection";
import { AdminRunTrajectoryReplay } from "./AdminRunTrajectoryReplay";
import { FailureGuidanceCard } from "../common/FailureGuidanceCard";
import { buildAdminFailureGuidance } from "./runFailureGuidance";

const RUN_LIMIT = 50;
const PAGE_SIZE = 10;
const DIAGNOSTIC_PAGE_SIZE = 20;
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
type RunDiagnosticView = "events" | "stages";

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
    "bg-[var(--theme-danger-soft)] text-[var(--theme-danger)] ring-[var(--theme-danger-ring)]",
  cancelled:
    "bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)] ring-[var(--theme-border)]",
  cancel_requested:
    "bg-[var(--theme-warning-soft)] text-[var(--theme-warning)] ring-[var(--theme-warning-ring)]",
  denied:
    "bg-[var(--theme-danger-soft)] text-[var(--theme-danger)] ring-[var(--theme-danger-ring)]",
};

const QUEUE_REASON_LABELS: Record<string, string> = {
  worker_available: "Worker 可接单",
  workers_busy: "Worker 忙碌",
  worker_capacity_full: "Worker 容量已满",
  queued_behind_existing_work: "等待前序任务",
  tenant_quota_full: "企业并发已满",
  user_quota_full: "用户并发已满",
  processing_lease_reclaimable: "存在可回收任务",
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
    if (status !== "all" && run.status !== status) return false;
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

export function summarizeAdminRuns(runs: AdminRunSummary[]) {
  return {
    queued: runs.filter((run) => run.status === "queued").length,
    running: runs.filter((run) => run.status === "running").length,
    failed: runs.filter((run) => run.status === "failed").length,
  };
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

function queueReasonLabel(reason: string | undefined): string {
  return reason ? QUEUE_REASON_LABELS[reason] ?? reason : "暂无队列信号";
}

function numericValue(value: number | null | undefined): string {
  return typeof value === "number" ? String(value) : "-";
}

function dateTime(value: string | null | undefined): string {
  return value ? formatDateTimeShort(value) : "-";
}

function durationLabel(run: AdminRunSummary, now = Date.now()): string {
  const started = run.started_at ?? run.queued_at ?? run.created_at;
  if (!started) return "-";
  const startMs = Date.parse(started);
  const endMs = run.finished_at ? Date.parse(run.finished_at) : now;
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs < startMs) {
    return "-";
  }
  const seconds = Math.max(0, Math.round((endMs - startMs) / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分 ${seconds % 60} 秒`;
  const hours = Math.floor(minutes / 60);
  return `${hours} 小时 ${minutes % 60} 分`;
}

function durationMillisecondsLabel(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "";
  if (value < 1_000) return `${Math.round(value)} 毫秒`;
  const seconds = Math.round(value / 1_000);
  if (seconds < 60) return `${seconds} 秒`;
  return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

function tokenCountLabel(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "未记录";
  return new Intl.NumberFormat("zh-CN").format(value);
}

function estimatedCostLabel(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "未记录";
  return `${new Intl.NumberFormat("zh-CN").format(value)} 最小计价单位`;
}

function diagnosticCoverageLabel(value: string): string {
  return (
    {
      full: "完整采集",
      partial: "部分采集",
      not_collected: "未采集",
      legacy_record: "历史记录",
      unsupported_schema: "版本暂不支持",
      transport_unavailable: "传输不可用",
    }[value] ?? value
  );
}

const DIAGNOSTIC_STAGE_LABELS: Record<string, string> = {
  attachment_materialization: "准备附件",
  skill_staging: "准备 Skill",
  sandbox_preparation: "准备运行环境",
  sandbox_submission: "提交执行任务",
  model_wait: "等待模型响应",
  artifact_validation: "检查产物",
  artifact_recovery: "恢复产物",
  terminalization: "整理最终结果",
};

const DIAGNOSTIC_SOURCE_LABELS: Record<string, string> = {
  sdk_result_error: "模型执行返回错误",
  sandbox_terminal_normalization: "沙箱终态整理",
  executor_reconciler: "执行结果对账",
};

function diagnosticLocation(stage: string | null | undefined, source: string | null | undefined): string {
  return [
    stage ? DIAGNOSTIC_STAGE_LABELS[stage] ?? stage : "阶段未知",
    source ? DIAGNOSTIC_SOURCE_LABELS[source] ?? source : "来源未知",
  ].join(" · ");
}

function elapsedBetween(
  startedAt: string | null | undefined,
  finishedAt: string | null | undefined,
): string {
  if (!startedAt || !finishedAt) return "耗时未知";
  const duration = Date.parse(finishedAt) - Date.parse(startedAt);
  return durationMillisecondsLabel(duration) || "耗时未知";
}

function byteSizeLabel(value: number): string {
  if (!Number.isFinite(value) || value < 0) return "-";
  if (value < 1_024) return `${value} B`;
  if (value < 1_048_576) return `${(value / 1_024).toFixed(1)} KB`;
  return `${(value / 1_048_576).toFixed(1)} MB`;
}

function stepOutput(step: AdminRunDetailResponse["steps"][number]): string {
  const output = step.payload?.output;
  return typeof output === "string" ? output.trim() : "";
}

function latestQueueInsight(runs: AdminRunSummary[]): AdminQueueInsight | null {
  return (
    runs.find((run) => run.queue_insight)?.queue_insight ?? null
  );
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

function timelineStatusTone(status: AdminRunTimelineItem["status"]): string {
  if (status === "failed" || status === "denied") {
    return "bg-[var(--theme-danger-soft)] text-[var(--theme-danger)]";
  }
  if (status === "succeeded") {
    return "bg-[var(--theme-success-soft)] text-[var(--theme-success)]";
  }
  if (status === "cancelled") {
    return "bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)]";
  }
  if (status === "running") {
    return "bg-[var(--theme-info-soft)] text-[var(--theme-info)]";
  }
  return "bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)]";
}

function timelineCountLabel(item: AdminRunTimelineItem): string {
  return item.count > 1 ? ` · ${item.count} 次合并` : "";
}

function diagnosticLengthLabel(item: AdminRunEventDiagnostic): string {
  const values = [
    item.deltaLength === null ? null : `增量 ${item.deltaLength} 字符`,
    item.textLength === null ? null : `累计 ${item.textLength} 字符`,
    item.deltaCount === null ? null : `${item.deltaCount} 个增量`,
  ].filter(Boolean);
  return values.join(" · ") || "无正文长度字段";
}

function MetricTile({
  icon,
  label,
  value,
  detail,
}: {
  icon: React.ReactNode;
  label: string;
  value: string | number;
  detail: string;
}) {
  return (
    <section className="flex min-w-0 items-center gap-2 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-3 py-2.5 shadow-[0_1px_2px_rgba(18,38,63,0.04)]">
      <span className="flex size-9 shrink-0 items-center justify-center rounded-md bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]">
        {icon}
      </span>
      <div className="min-w-0">
        <div className="flex items-baseline gap-1.5">
          <span className="text-lg font-semibold tabular-nums text-[var(--theme-text)]">
            {value}
          </span>
          <span className="whitespace-nowrap text-xs font-medium text-[var(--theme-text-secondary)]">
            {label}
          </span>
        </div>
        <p className="truncate text-[11px] text-[var(--theme-text-tertiary)]">
          {detail}
        </p>
      </div>
    </section>
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
          <button
            type="button"
            className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] hover:bg-[var(--theme-bg-sidebar)]"
            onClick={() => void copyValue()}
            aria-label={`复制${label}`}
          >
            <Copy size={11} />
            {copied ? "已复制" : "复制"}
          </button>
        ) : null}
      </dt>
      <dd className="mt-1 break-all font-mono text-xs leading-5 text-[var(--theme-text)]" title={value || undefined}>
        {value || "-"}
      </dd>
    </div>
  );
}

function FailureTraceOverview({
  run,
  diagnostics,
  leases,
  loading,
}: {
  run: AdminRunSummary;
  diagnostics: AdminRunDiagnosticsResponse | null;
  leases: AdminRunDetailResponse["sandbox_leases"];
  loading: boolean;
}) {
  if (loading && !diagnostics) {
    return <p className="mt-3 text-xs text-[var(--theme-text-secondary)]">正在定位失败断点…</p>;
  }
  if (!diagnostics) {
    return (
      <section className="mt-3 rounded-md border border-[var(--theme-border)] p-3 text-xs text-[var(--theme-text-secondary)]" data-run-failure-trace>
        失败断点暂不可用；请重新读取诊断，不能仅凭终态错误码推断失败阶段。
      </section>
    );
  }
  const root = diagnostics.root;
  const attempt = root?.attempt_id
    ? diagnostics.attempts.find((item) => item.attempt_id === root.attempt_id)
    : null;
  const handling = diagnostics.handling.at(-1);
  const lease = root?.lease_id
    ? leases.find((item) => (item.lease_id ?? item.id) === root.lease_id)
    : null;
  const rootEvidence = root?.observation_id
    ? diagnostics.details.observations?.find((item) => item.observation_id === root.observation_id)
    : null;
  const toolEvidence = rootEvidence?.tool_policy_denials.at(-1)
    ?? rootEvidence?.tool_calls.at(-1)
    ?? rootEvidence?.tool_lifecycles.at(-1);
  const evidenceGap = diagnostics.losses.length + diagnostics.counts.omitted_observations;
  return (
    <section className="mt-3 rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] p-3" data-run-failure-trace>
      <h4 className="text-xs font-semibold text-[var(--theme-text)]">故障链 · 已留存证据</h4>
      <dl className="mt-2 grid gap-2 text-xs sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
        <div>
          <dt className="text-[11px] text-[var(--theme-text-tertiary)]">最早留存的断点</dt>
          <dd className="mt-0.5 break-words font-medium text-[var(--theme-text)]">
            {root ? diagnosticLocation(root.stage, root.source) : "断点未知"}
          </dd>
          <dd className="mt-0.5 text-[11px] text-[var(--theme-text-secondary)]">
            {root ? (attempt ? `第 ${attempt.ordinal} 次尝试` : "执行尝试未能关联") : "没有留存失败观察"}
          </dd>
          {root ? (
            <dd className="mt-0.5 font-mono text-[10px] text-[var(--theme-text-tertiary)]">
              {[root.stage, root.source].filter(Boolean).join(" · ")}
            </dd>
          ) : null}
          {root?.message ? (
            <dd className="mt-1 break-words text-[11px] text-[var(--theme-text-secondary)]">
              留存异常摘要：{root.message}
            </dd>
          ) : null}
        </div>
        <div>
          <dt className="text-[11px] text-[var(--theme-text-tertiary)]">错误如何变化</dt>
          <dd className="mt-0.5 break-all font-mono text-[11px] text-[var(--theme-text)]">
            {root?.error_code || "来源错误未知"} → {run.status === "running" || run.status === "queued" ? "运行尚未终态" : run.error_code || "终态错误未知"}
          </dd>
          <dd className="mt-0.5 text-[11px] text-[var(--theme-text-secondary)]">
            {handling ? `最近处理：${handling.stage || "阶段未知"} · ${handling.error_code || "错误码未知"}` : "没有后续处理观察"}
          </dd>
        </div>
        {toolEvidence ? (
          <div>
            <dt className="text-[11px] text-[var(--theme-text-tertiary)]">同一观察中的工具证据</dt>
            <dd className="mt-0.5 break-words text-[var(--theme-text)]">
              {[toolEvidence.tool_name, toolEvidence.last_stage || toolEvidence.state, toolEvidence.reason].filter(Boolean).join(" · ")}
            </dd>
          </div>
        ) : null}
        {root?.lease_id ? (
          <div>
            <dt className="text-[11px] text-[var(--theme-text-tertiary)]">关联运行环境</dt>
            <dd className="mt-0.5 text-[var(--theme-text)]">
              {lease ? `${lease.provider || "平台沙箱"} · ${statusLabel(lease.status)}` : "运行环境未能关联"}
            </dd>
          </div>
        ) : null}
        <div>
          <dt className="text-[11px] text-[var(--theme-text-tertiary)]">证据完整性</dt>
          <dd className="mt-0.5 text-[var(--theme-text)]">
            {diagnosticCoverageLabel(diagnostics.coverage)} · {diagnostics.counts.retained_observations} 条观察
          </dd>
          <dd className="mt-0.5 text-[11px] text-[var(--theme-text-secondary)]">
            {evidenceGap ? `${evidenceGap} 项缺失或裁剪` : "未报告证据缺口"}
          </dd>
        </div>
      </dl>
      <p className="mt-2 text-[11px] text-[var(--theme-text-tertiary)]">
        最早留存异常不等于已证明的根因；缺失证据不能据此补全。
      </p>
    </section>
  );
}

type ExecutionJournalEntry =
  | { kind: "tool"; sequence: number; ordinal: number; action: AdminWorkerExecution["actions"][number] }
  | { kind: "message"; sequence: number; ordinal: number; message: NonNullable<AdminWorkerExecution["messages"]>[number] };

function executionJournal(execution: AdminWorkerExecution): ExecutionJournalEntry[] {
  const actions: ExecutionJournalEntry[] = execution.actions.map((action) => ({
    kind: "tool",
    sequence: action.sequence ?? Number.MAX_SAFE_INTEGER,
    ordinal: action.ordinal,
    action,
  }));
  const messages: ExecutionJournalEntry[] = (execution.messages ?? []).map((message) => ({
    kind: "message",
    sequence: message.sequence,
    ordinal: message.ordinal,
    message,
  }));
  return [...actions, ...messages].sort((left, right) =>
    left.sequence - right.sequence || left.ordinal - right.ordinal,
  );
}

function AttemptSection({ diagnostics }: { diagnostics: AdminRunDiagnosticsResponse | null }) {
  const attempts = diagnostics?.attempts ?? [];
  if (!attempts.length) return null;
  const currentAttempt = attempts.at(-1)?.attempt_id;
  return (
    <section className="p-4" data-run-attempts>
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-xs font-semibold text-[var(--theme-text)]">执行尝试</h3>
        <span className="text-[11px] text-[var(--theme-text-tertiary)]">共 {attempts.length} 次</span>
      </div>
      <ol className="mt-3 space-y-2">
        {attempts.map((attempt) => (
          <li key={attempt.attempt_id} className="rounded-md border border-[var(--theme-border)] p-2.5">
            <div className="flex items-center justify-between gap-2">
              <p className="text-xs font-medium text-[var(--theme-text)]">
                第 {attempt.ordinal} 次尝试
                {attempt.attempt_id === currentAttempt ? " · 当前" : ""}
              </p>
              <StatusBadge status={attempt.status} />
            </div>
            <p className="mt-1 text-[11px] text-[var(--theme-text-secondary)]">
              {[attempt.owner_kind, elapsedBetween(attempt.started_at, attempt.finished_at), attempt.terminal_reason]
                .filter(Boolean)
                .join(" · ")}
            </p>
            {attempt.error_code ? (
              <p className="mt-1 break-words font-mono text-[11px] text-[var(--theme-danger)]">{attempt.error_code}</p>
            ) : null}
            <details className="mt-1 text-[10px] text-[var(--theme-text-tertiary)]">
              <summary className="cursor-pointer">尝试编号</summary>
              <p className="mt-1 break-all font-mono">{attempt.attempt_id}</p>
            </details>
          </li>
        ))}
      </ol>
    </section>
  );
}

function SemanticTimeline({ items }: { items: AdminRunTimelineItem[] }) {
  return (
    <section className="p-4" data-run-semantic-timeline>
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-xs font-semibold text-[var(--theme-text)]">处理时间线</h3>
        <span className="text-[11px] text-[var(--theme-text-tertiary)]">{items.length} 个语义阶段</span>
      </div>
      {items.length ? (
        <ol className="mt-3 space-y-2">
          {items.map((item) => (
            <li key={item.id} className="grid grid-cols-[10px_minmax(0,1fr)] gap-3">
              <span className={`mt-1.5 size-2 rounded-full ${timelineStatusTone(item.status)}`} />
              <div className="min-w-0 rounded-md bg-[var(--theme-bg-sidebar)] p-2.5">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className="text-xs font-medium text-[var(--theme-text)]">
                    {item.label}{timelineCountLabel(item)}
                  </span>
                  <time dateTime={item.created_at ?? undefined} className="ml-auto text-[11px] text-[var(--theme-text-tertiary)]">
                    {dateTime(item.created_at)}
                  </time>
                </div>
                <p className="mt-1 text-[11px] text-[var(--theme-text-tertiary)]">
                  {[item.stage, durationMillisecondsLabel(item.duration_ms)].filter(Boolean).join(" · ") || "未记录阶段耗时"}
                </p>
                {item.detail ? (
                  <p className="mt-1 text-[11px] leading-5 text-[var(--theme-text-secondary)]">{item.detail}</p>
                ) : null}
              </div>
            </li>
          ))}
        </ol>
      ) : (
        <p className="mt-3 text-xs text-[var(--theme-text-tertiary)]">暂无可展示的处理阶段</p>
      )}
    </section>
  );
}

function focusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(
    container.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  );
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
  onClose: () => void;
  fallbackFocusRef: RefObject<HTMLButtonElement | null>;
}) {
  const detailRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);
  const [diagnosticView, setDiagnosticView] = useState<RunDiagnosticView>("events");
  const [diagnosticPage, setDiagnosticPage] = useState(0);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    setDiagnosticView("events");
    setDiagnosticPage(0);
  }, [detail?.run.run_id]);

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
    ? buildAdminRunMonitorView(detail.run, detail.events, diagnostics)
    : null;
  const workerExecution = detail?.worker_execution ?? {
    response: monitorView?.modelOutput ?? "",
    messages: [],
    actions: [],
    model: {},
  };
  const journal = executionJournal(workerExecution);
  const toolEvidenceById = new Map(
    (diagnostics?.details.observations ?? [])
      .flatMap((observation) => [
        ...observation.tool_lifecycles,
        ...observation.tool_calls,
        ...observation.tool_policy_denials,
      ])
      .filter((evidence) => evidence.invocation_id)
      .map((evidence) => [evidence.invocation_id, evidence] as const),
  );
  const artifacts = detail?.artifacts ?? [];
  const failureGuidance = detail ? buildAdminFailureGuidance(detail) : null;
  const eventDiagnostics = monitorView?.eventDiagnostics ?? [];
  const diagnosticPageCount = Math.max(
    1,
    Math.ceil(eventDiagnostics.length / DIAGNOSTIC_PAGE_SIZE),
  );
  const currentDiagnosticPage = Math.min(diagnosticPage, diagnosticPageCount - 1);
  const visibleEventDiagnostics = eventDiagnostics.slice(
    currentDiagnosticPage * DIAGNOSTIC_PAGE_SIZE,
    (currentDiagnosticPage + 1) * DIAGNOSTIC_PAGE_SIZE,
  );

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
        <button
          ref={closeRef}
          type="button"
          className="btn-icon flex size-9 shrink-0 items-center justify-center rounded-md"
          onClick={onClose}
          aria-label="关闭运行详情"
          title="关闭运行详情"
        >
          <X size={17} />
        </button>
      </div>

      {loading ? (
        <div className="flex min-h-64 items-center justify-center">
          <PanelLoadingState text="正在读取运行详情" />
        </div>
      ) : error ? (
        <div className="p-4">
          <div
            role="alert"
            className="border-l-2 border-l-[var(--theme-danger)] bg-[var(--theme-danger-soft)] px-3 py-2 text-sm text-[var(--theme-danger)]"
          >
            {error}
          </div>
        </div>
      ) : detail ? (
        <div className="divide-y divide-[var(--theme-border)]">
          <section className="p-4" data-worker-execution-result>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-xs font-semibold text-[var(--theme-text)]">
                执行结果
              </h3>
              <StatusBadge status={detail.run.status} />
            </div>
            <p className="mt-2 text-sm font-medium text-[var(--theme-text)]">
              {monitorView?.currentAction}
            </p>
            <p className="mt-1 text-xs text-[var(--theme-text-secondary)]">
              {userDisplayName(detail.run)} · {workspaceDisplayName(detail.run)} ·{" "}
              {skillDisplayName(detail.run)}
            </p>
            <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
              <div className="rounded-md bg-[var(--theme-bg-sidebar)] p-2">
                <span className="text-[var(--theme-text-tertiary)]">开始</span>
                <p className="mt-1 text-[var(--theme-text)]">
                  {dateTime(detail.run.started_at ?? detail.run.queued_at)}
                </p>
              </div>
              <div className="rounded-md bg-[var(--theme-bg-sidebar)] p-2">
                <span className="text-[var(--theme-text-tertiary)]">耗时</span>
                <p className="mt-1 text-[var(--theme-text)]">
                  {durationMillisecondsLabel(detail.run.latency_ms) || durationLabel(detail.run)}
                </p>
              </div>
              <div className="rounded-md bg-[var(--theme-bg-sidebar)] p-2">
                <span className="text-[var(--theme-text-tertiary)]">Token</span>
                <p className="mt-1 text-[var(--theme-text)]">{tokenCountLabel(detail.run.total_token_count)}</p>
              </div>
              <div className="rounded-md bg-[var(--theme-bg-sidebar)] p-2">
                <span className="text-[var(--theme-text-tertiary)]">估算成本</span>
                <p className="mt-1 text-[var(--theme-text)]">{estimatedCostLabel(detail.run.estimated_cost_minor)}</p>
              </div>
            </div>
            {detail.run.status === "failed" || diagnostics?.root ? (
              <FailureTraceOverview
                run={detail.run}
                diagnostics={diagnostics}
                leases={detail.sandbox_leases}
                loading={diagnosticsLoading}
              />
            ) : null}
            {failureGuidance ? (
              <FailureGuidanceCard
                guidance={failureGuidance}
                className="mt-3"
              />
            ) : null}
            <details className="mt-4 rounded-md border border-[var(--theme-border)] px-3 py-2">
              <summary className="cursor-pointer text-xs font-medium text-[var(--theme-text-secondary)]">
                技术信息
              </summary>
              <p className="mt-2 text-[11px] leading-5 text-[var(--theme-text-tertiary)]">
                以下标识用于精确检索、跨系统关联和研发排查。
              </p>
              {detail.run.error_message ? (
                <p className="mt-2 break-words text-[11px] text-[var(--theme-text-secondary)]">
                  终态记录说明（不等于失败断点）：{detail.run.error_message}
                </p>
              ) : null}
              <dl className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
                <IdentityField label="Run ID" value={detail.run.run_id} />
                <IdentityField label="Session ID" value={detail.run.session_id} />
                <IdentityField
                  label={detail.run.trace_id_recorded ? "已记录 Trace ID" : "派生关联 ID"}
                  value={detail.run.trace_id}
                />
                <IdentityField label="用户 ID" value={detail.run.user_id} />
                <IdentityField label="工作区 ID" value={detail.run.workspace_id} />
                <IdentityField label="Agent ID" value={detail.run.agent_id} />
                <IdentityField label="Skill ID" value={detail.run.skill_id} />
                <IdentityField label="来源 Run ID" value={detail.run.copied_from_run_id} />
              </dl>
            </details>
          </section>

          <AttemptSection diagnostics={diagnostics} />

          <section className="p-4" data-worker-execution-content>
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h3 className="text-xs font-semibold text-[var(--theme-text)]">
                Agent 与工具记录
              </h3>
              <span className="text-[11px] text-[var(--theme-text-tertiary)]">
                {[
                  typeof workerExecution.model.turn_count === "number"
                    ? `模型 ${workerExecution.model.turn_count} 轮`
                    : null,
                  durationMillisecondsLabel(workerExecution.model.duration_ms),
                ].filter(Boolean).join(" · ") || "公开执行记录"}
              </span>
            </div>

            <p className="mt-2 text-[11px] text-[var(--theme-text-tertiary)]">
              {workerExecution.messages?.length ?? 0} 条公开 Agent 输出 · {workerExecution.actions.length} 次工具调用。工具仅显示脱敏后的执行概要。
            </p>
            {journal.length ? (
              <ol className="mt-3 space-y-2" data-run-execution-journal>
                {journal.map((entry) => entry.kind === "message" ? (
                  <li key={`message-${entry.ordinal}`} className="rounded-md border border-[var(--theme-border)] p-3" data-run-agent-output>
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-semibold text-[var(--theme-text)]">
                        {entry.message.kind === "commentary" ? "Agent 过程说明" : "Agent 输出"} {entry.ordinal}
                      </span>
                      <time className="text-[11px] text-[var(--theme-text-tertiary)]">{dateTime(entry.message.created_at)}</time>
                    </div>
                    <p className="mt-2 whitespace-pre-wrap break-words text-xs leading-5 text-[var(--theme-text-secondary)]">
                      {entry.message.text}
                    </p>
                  </li>
                ) : (
                  <li key={`tool-${entry.ordinal}`} className="rounded-md bg-[var(--theme-bg-sidebar)] p-2.5" data-run-tool-call>
                    <div className="flex items-center gap-2">
                      <span className="min-w-0 flex-1 truncate text-xs font-medium text-[var(--theme-text)]">
                        工具调用 {entry.ordinal} · {entry.action.label}
                      </span>
                      <StatusBadge status={entry.action.status} />
                    </div>
                    <p className="mt-1 text-[11px] text-[var(--theme-text-tertiary)]">
                      {[entry.action.category, durationMillisecondsLabel(entry.action.duration_ms)].filter(Boolean).join(" · ") || "耗时未知"}
                    </p>
                    {entry.action.input_summary ? (
                      <p className="mt-1.5 text-xs leading-5 text-[var(--theme-text-secondary)]">执行：{entry.action.input_summary}</p>
                    ) : null}
                    {entry.action.result_summary ? (
                      <p className="mt-1 text-xs leading-5 text-[var(--theme-text)]">结果：{entry.action.result_summary}</p>
                    ) : null}
                    {entry.action.invocation_id && toolEvidenceById.has(entry.action.invocation_id) ? (
                      <p className="mt-1 text-xs leading-5 text-[var(--theme-text)]">
                        关联诊断：{[
                          toolEvidenceById.get(entry.action.invocation_id)?.last_stage,
                          toolEvidenceById.get(entry.action.invocation_id)?.state,
                          toolEvidenceById.get(entry.action.invocation_id)?.reason,
                        ].filter(Boolean).join(" · ") || "已找到同一调用的证据"}
                      </p>
                    ) : null}
                    {entry.action.invocation_id ? (
                      <details className="mt-1 text-[11px] text-[var(--theme-text-tertiary)]">
                        <summary className="cursor-pointer">调用编号</summary>
                        <p className="mt-1 break-all font-mono">{entry.action.invocation_id}</p>
                      </details>
                    ) : null}
                  </li>
                ))}
              </ol>
            ) : null}
            {!workerExecution.messages?.length && workerExecution.response ? (
              <div className="mt-3 rounded-md border border-[var(--theme-border)] p-3">
                <h4 className="text-xs font-semibold text-[var(--theme-text)]">Worker 返回</h4>
                <p className="mt-2 max-h-80 overflow-y-auto whitespace-pre-wrap break-words text-xs leading-5 text-[var(--theme-text-secondary)]">
                  {workerExecution.response}
                </p>
              </div>
            ) : !journal.length ? (
              <p className="mt-3 text-xs text-[var(--theme-text-tertiary)]">尚无可展示的 Agent 输出或工具调用</p>
            ) : null}

            {artifacts.length ? (
              <div className="mt-3">
                <h4 className="text-xs font-semibold text-[var(--theme-text)]">
                  有效产物 ({artifacts.length})
                </h4>
                <ul className="mt-2 space-y-2">
                  {artifacts.map((artifact) => (
                    <li
                      key={artifact.artifact_id}
                      className="rounded-md bg-[var(--theme-bg-sidebar)] p-2.5"
                    >
                      <p className="break-words text-xs font-medium text-[var(--theme-text)]">
                        {artifact.label}
                      </p>
                      <p className="mt-1 text-[11px] text-[var(--theme-text-tertiary)]">
                        {[artifact.artifact_type, artifact.content_type, byteSizeLabel(artifact.size_bytes)]
                          .filter(Boolean)
                          .join(" · ")}
                      </p>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </section>

          <AdminRunTrajectoryReplay
            key={detail.run.run_id}
            runId={detail.run.run_id}
            attempts={diagnostics?.attempts ?? []}
            messages={workerExecution.messages ?? []}
          />

          <SemanticTimeline items={monitorView?.recentActivity ?? []} />

          <details className="p-4">
            <summary className="cursor-pointer text-xs font-semibold text-[var(--theme-text)]">
              执行诊断与处理证据
            </summary>
            <div className="-mx-4 -mb-4 mt-3 border-t border-[var(--theme-border)]">
              <RunDiagnosticsSection
                diagnostics={diagnostics}
                loading={diagnosticsLoading}
                error={diagnosticsError}
                exporting={diagnosticsExporting}
                exportError={diagnosticsExportError}
                onRetry={onRetryDiagnostics}
                onExport={onExportDiagnostics}
              />
            </div>
          </details>

          <details className="p-4">
            <summary className="cursor-pointer text-xs font-semibold text-[var(--theme-text)]">
              事件协议细节
            </summary>
            <div className="mt-3">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <div>
                <h3 className="text-xs font-semibold text-[var(--theme-text)]">事件诊断</h3>
                <p className="mt-1 text-[11px] text-[var(--theme-text-tertiary)]">
                  仅显示公开消息和终态事件的身份与长度统计，不显示事件正文或 SDK 原始日志。
                </p>
              </div>
              <span className="text-[11px] text-[var(--theme-text-tertiary)]">
                已记录 {monitorView?.rawEventCount ?? detail.events.length} 个事件
              </span>
            </div>
            <div
              className="mt-3 inline-flex rounded-md border border-[var(--theme-border)] p-0.5"
              role="tablist"
              aria-label="运行诊断视图"
            >
              <button
                type="button"
                role="tab"
                aria-selected={diagnosticView === "events"}
                className={`rounded px-2.5 py-1.5 text-xs font-medium ${diagnosticView === "events" ? "bg-[var(--theme-info-soft)] text-[var(--theme-info)]" : "text-[var(--theme-text-secondary)]"}`}
                onClick={() => setDiagnosticView("events")}
              >
                消息事件
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={diagnosticView === "stages"}
                className={`rounded px-2.5 py-1.5 text-xs font-medium ${diagnosticView === "stages" ? "bg-[var(--theme-info-soft)] text-[var(--theme-info)]" : "text-[var(--theme-text-secondary)]"}`}
                onClick={() => setDiagnosticView("stages")}
              >
                关键阶段
              </button>
            </div>
            {diagnosticView === "events" ? (
              eventDiagnostics.length ? (
                <>
                  <ol className="mt-3 space-y-2" data-run-event-diagnostics>
                    {visibleEventDiagnostics.map((item) => (
                      <li
                        key={`${item.id}:${item.sequence ?? "na"}`}
                        className="rounded-md bg-[var(--theme-bg-sidebar)] p-2.5"
                      >
                        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                          <span className="font-mono text-xs font-medium text-[var(--theme-text)]">
                            {item.type}
                          </span>
                          <span className="font-mono text-[11px] text-[var(--theme-text-tertiary)]">
                            seq {item.sequence ?? "-"}
                          </span>
                          <time className="ml-auto text-[11px] text-[var(--theme-text-tertiary)]">
                            {dateTime(item.created_at)}
                          </time>
                        </div>
                        <div className="mt-1.5 grid gap-x-3 gap-y-1 text-[11px] text-[var(--theme-text-secondary)] sm:grid-cols-2">
                          <span>message_id: <code>{item.messageId ?? "-"}</code></span>
                          <span>stream: <code>{item.streamIncarnation ?? "-"}</code></span>
                          <span>{diagnosticLengthLabel(item)}</span>
                          <span>event_id: <code>{item.id}</code></span>
                        </div>
                        {item.errorCode || item.severity ? (
                          <p className="mt-1 text-[11px] text-[var(--theme-danger)]">
                            {[item.severity, item.errorCode].filter(Boolean).join(" · ")}
                          </p>
                        ) : null}
                      </li>
                    ))}
                  </ol>
                  {diagnosticPageCount > 1 ? (
                    <div className="mt-3 flex items-center justify-between gap-2 text-[11px] text-[var(--theme-text-secondary)]">
                      <button
                        type="button"
                        aria-label="上一页事件"
                        className="rounded-md border border-[var(--theme-border)] px-2 py-1 disabled:cursor-not-allowed disabled:opacity-40"
                        disabled={currentDiagnosticPage === 0}
                        onClick={() => setDiagnosticPage((page) => Math.max(0, page - 1))}
                      >
                        上一页
                      </button>
                      <span>
                        第 {currentDiagnosticPage + 1} / {diagnosticPageCount} 页 · 共 {eventDiagnostics.length} 条
                      </span>
                      <button
                        type="button"
                        aria-label="下一页事件"
                        className="rounded-md border border-[var(--theme-border)] px-2 py-1 disabled:cursor-not-allowed disabled:opacity-40"
                        disabled={currentDiagnosticPage >= diagnosticPageCount - 1}
                        onClick={() => setDiagnosticPage((page) => Math.min(diagnosticPageCount - 1, page + 1))}
                      >
                        下一页
                      </button>
                    </div>
                  ) : null}
                </>
              ) : (
                <p className="mt-3 text-xs text-[var(--theme-text-tertiary)]">暂无消息或终态事件</p>
              )
            ) : monitorView?.recentActivity.length ? (
              <ol className="mt-3 space-y-2">
                {monitorView.recentActivity.map((item) => (
                  <li
                    key={item.id}
                    className="grid grid-cols-[10px_minmax(0,1fr)] gap-3"
                  >
                    <span className={`mt-1.5 size-2 rounded-full ${timelineStatusTone(item.status)}`} />
                    <div className="min-w-0 rounded-md bg-[var(--theme-bg-sidebar)] p-2.5">
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <span className="text-xs font-medium text-[var(--theme-text)]">
                          {item.label}{timelineCountLabel(item)}
                        </span>
                        <time className="ml-auto text-[11px] text-[var(--theme-text-tertiary)]">
                          {dateTime(item.created_at)}
                        </time>
                      </div>
                      {item.detail ? (
                        <p className="mt-1 text-[11px] leading-5 text-[var(--theme-text-secondary)]">
                          {item.detail}
                        </p>
                      ) : null}
                    </div>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="mt-3 text-xs text-[var(--theme-text-tertiary)]">暂无可展示的关键阶段</p>
            )}
            </div>
          </details>

          {detail.steps.length ? (
            <section className="p-4">
              <h3 className="text-xs font-semibold text-[var(--theme-text)]">
                执行步骤 <span className="font-normal text-[var(--theme-text-tertiary)]">({detail.steps.length})</span>
              </h3>
              <div className="mt-3 space-y-2">
                {detail.steps.map((step, index) => (
                  <div
                    key={step.step_id ?? `${step.step_kind ?? "step"}-${index}`}
                    className="flex items-start justify-between gap-3 rounded-md bg-[var(--theme-bg-sidebar)] p-2.5"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-xs font-medium text-[var(--theme-text)]">
                        {step.title ?? step.step_kind ?? "执行步骤"}
                      </p>
                      {stepOutput(step) ? (
                        <p className="mt-1 whitespace-pre-wrap break-words text-xs leading-5 text-[var(--theme-text-secondary)]">
                          返回：{stepOutput(step)}
                        </p>
                      ) : null}
                      <p className="mt-1 text-[11px] text-[var(--theme-text-tertiary)]">
                        {dateTime(step.started_at)} · {durationLabel({
                          ...detail.run,
                          started_at: step.started_at,
                          queued_at: null,
                          created_at: null,
                          finished_at: step.finished_at,
                        })}
                      </p>
                    </div>
                    <StatusBadge status={step.status} />
                  </div>
                ))}
              </div>
            </section>
          ) : null}

          <details className="p-4">
            <summary className="cursor-pointer text-xs font-semibold text-[var(--theme-text)]">
              沙箱租约 ({detail.sandbox_leases.length})
            </summary>
            {detail.sandbox_leases.length ? (
              <div className="mt-3 space-y-2">
                {detail.sandbox_leases.map((lease, index) => (
                  <div
                    key={lease.lease_id ?? lease.id ?? `lease-${index}`}
                    className="rounded-md border border-[var(--theme-border)] p-2.5"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-xs font-medium text-[var(--theme-text)]">
                        运行环境 {index + 1} · {lease.provider || "平台沙箱"}
                      </span>
                      <StatusBadge status={lease.status} />
                    </div>
                    <p className="mt-1.5 text-[11px] text-[var(--theme-text-tertiary)]">
                      {lease.sandbox_mode || "运行模式未知"}
                    </p>
                    <details className="mt-1 text-[10px] text-[var(--theme-text-tertiary)]">
                      <summary className="cursor-pointer">租约编号</summary>
                      <p className="mt-1 break-all font-mono">{lease.lease_id ?? lease.id ?? "未记录"}</p>
                    </details>
                  </div>
                ))}
              </div>
            ) : (
              <p className="mt-2 text-xs text-[var(--theme-text-tertiary)]">
                此运行没有沙箱租约
              </p>
            )}
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
      <table className="w-full border-separate border-spacing-0 text-left text-xs">
        <thead className="sticky top-0 z-[1] bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)]">
          <tr>
            <th className="border-b border-[var(--theme-border)] px-3 py-2.5 font-medium">状态</th>
            <th className="border-b border-[var(--theme-border)] px-3 py-2.5 font-medium">任务 / 运行</th>
            <th className="border-b border-[var(--theme-border)] px-3 py-2.5 font-medium">用户 / 工作区</th>
            <th className="border-b border-[var(--theme-border)] px-3 py-2.5 font-medium">Agent / Skill</th>
            <th className="border-b border-[var(--theme-border)] px-3 py-2.5 font-medium">时间</th>
            <th className="w-10 border-b border-[var(--theme-border)] px-2 py-2.5"><span className="sr-only">详情</span></th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => {
            const selected = run.run_id === selectedRunId;
            return (
              <tr
                key={run.run_id}
                data-selected={selected ? "true" : "false"}
                className="group transition-colors hover:bg-[var(--theme-bg-sidebar)] data-[selected=true]:bg-[var(--theme-info-soft)]"
              >
                <td className="border-b border-[var(--theme-border)] px-3 py-3 align-top">
                  <StatusBadge status={run.status} />
                  {run.status === "queued" && run.queue_position ? (
                    <p className="mt-1.5 whitespace-nowrap text-[11px] text-[var(--theme-text-tertiary)]">
                      队列第 {run.queue_position} 位
                    </p>
                  ) : null}
                  {run.error_code ? (
                    <p
                      className="mt-1.5 max-w-40 truncate font-mono text-[11px] text-[var(--theme-danger)]"
                      title={run.error_code}
                    >
                      {run.error_code}
                    </p>
                  ) : null}
                </td>
                <td className="max-w-[250px] border-b border-[var(--theme-border)] px-3 py-3 align-top">
                  <button
                    type="button"
                    onClick={() => onSelect(run.run_id)}
                    className="block w-full min-w-0 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-focus-ring)]"
                    aria-label={`查看 ${run.run_id}`}
                  >
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
                  </button>
                </td>
                <td className="max-w-[190px] border-b border-[var(--theme-border)] px-3 py-3 align-top">
                  <p className="truncate text-[var(--theme-text)]" title={userDisplayName(run)}>
                    {userDisplayName(run)}
                  </p>
                  <p
                    className="mt-1 truncate text-[11px] text-[var(--theme-text-tertiary)]"
                    title={workspaceDisplayName(run)}
                  >
                    {workspaceDisplayName(run)}
                  </p>
                </td>
                <td className="max-w-[210px] border-b border-[var(--theme-border)] px-3 py-3 align-top">
                  <p className="truncate text-[var(--theme-text)]" title={agentDisplayName(run)}>
                    {agentDisplayName(run)}
                  </p>
                  <p
                    className="mt-1 truncate text-[11px] text-[var(--theme-text-tertiary)]"
                    title={skillDisplayName(run)}
                  >
                    {skillDisplayName(run)}
                  </p>
                </td>
                <td className="whitespace-nowrap border-b border-[var(--theme-border)] px-3 py-3 align-top">
                  <p className="text-[var(--theme-text)]">{dateTime(run.started_at ?? run.queued_at ?? run.created_at)}</p>
                  <p className="mt-1 tabular-nums text-[11px] text-[var(--theme-text-tertiary)]">{durationLabel(run)}</p>
                </td>
                <td className="border-b border-[var(--theme-border)] px-2 py-3 align-middle">
                  <button
                    type="button"
                    onClick={() => onSelect(run.run_id)}
                    className="btn-icon flex size-8 items-center justify-center rounded-md"
                    aria-label={`打开 ${run.run_id} 详情`}
                    title="打开详情"
                  >
                    <ChevronRight size={16} />
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
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
        <button
          key={run.run_id}
          type="button"
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
            <p className="mt-2 truncate font-mono text-[11px] text-[var(--theme-danger)]">
              {run.error_code}
            </p>
          ) : null}
          <div className="mt-2 flex items-center justify-between gap-3 text-[11px] text-[var(--theme-text-secondary)]">
            <span className="truncate">{userDisplayName(run)} · {agentDisplayName(run)}</span>
            <span className="shrink-0 tabular-nums">{durationLabel(run)}</span>
          </div>
        </button>
      ))}
    </div>
  );
}

export function RunMonitorPanel() {
  const scope = useMemo(() => readAdminRunDeepLinkScope(), []);
  const [runs, setRuns] = useState<AdminRunSummary[]>([]);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(scope.runId);
  const [detail, setDetail] = useState<AdminRunDetailResponse | null>(null);
  const [diagnostics, setDiagnostics] = useState<AdminRunDiagnosticsResponse | null>(null);
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
  const detailRequestSequence = useRef(0);
  const diagnosticsRequestSequence = useRef(0);
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
    const requestId = ++listRequestSequence.current;
    if (initial) setIsLoading(true);
    else setIsRefreshing(true);
    try {
      const response = await adminRunsApi.list({
        limit: RUN_LIMIT,
        userId: scope.userId ?? undefined,
      });
      if (requestId !== listRequestSequence.current) return;
      setRuns(response.runs ?? []);
      setLoadError(null);
      setLastUpdatedAt(new Date());
      const activeRunId = selectedRunIdRef.current;
      if (activeRunId) {
        void loadDetail(activeRunId, false);
        void loadDiagnostics(activeRunId, false);
      }
    } catch (error) {
      if (requestId !== listRequestSequence.current) return;
      setLoadError(error instanceof Error ? error.message : "最近运行加载失败");
    } finally {
      if (requestId === listRequestSequence.current) {
        setIsLoading(false);
        setIsRefreshing(false);
      }
    }
  }, [loadDetail, loadDiagnostics, scope.userId]);

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

  const exportDiagnostics = useCallback(async () => {
    const runId = selectedRunIdRef.current;
    if (!runId) return;
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
      setDiagnosticsExportError(error instanceof Error ? error.message : "诊断包生成失败");
    } finally {
      setDiagnosticsExporting(false);
    }
  }, []);

  const selectRun = useCallback((runId: string) => {
    selectedRunIdRef.current = runId;
    setSelectedRunId(runId);
    setDetail(null);
    setDiagnostics(null);
    setDiagnosticsExportError(null);
    void loadDetail(runId);
    void loadDiagnostics(runId);
  }, [loadDetail, loadDiagnostics]);

  const closeDetail = useCallback(() => {
    detailRequestSequence.current += 1;
    diagnosticsRequestSequence.current += 1;
    selectedRunIdRef.current = null;
    setSelectedRunId(null);
    setDetail(null);
    setDiagnostics(null);
    setDetailError(null);
    setDiagnosticsError(null);
    setDiagnosticsExportError(null);
    setDiagnosticsExporting(false);
    setDetailLoading(false);
    setDiagnosticsLoading(false);
  }, []);

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
  const summary = useMemo(() => summarizeAdminRuns(runs), [runs]);
  const queueInsight = useMemo(() => latestQueueInsight(runs), [runs]);
  const lastUpdatedLabel = lastUpdatedAt
    ? lastUpdatedAt.toLocaleTimeString("zh-CN", { hour12: false })
    : "尚未刷新";

  const headerActions = (
    <div className="flex items-center gap-1">
      <button
        type="button"
        className="btn-icon flex size-9 items-center justify-center rounded-md"
        onClick={() => setAutoRefresh((value) => !value)}
        aria-label={autoRefresh ? "暂停自动刷新" : "开启自动刷新"}
        title={autoRefresh ? "暂停自动刷新" : "开启自动刷新"}
      >
        {autoRefresh ? <Pause size={16} /> : <Play size={16} />}
      </button>
      <button
        ref={refreshButtonRef}
        type="button"
        className="btn-icon flex size-9 items-center justify-center rounded-md"
        onClick={() => void loadRuns(false)}
        disabled={isRefreshing}
        aria-label="刷新最近运行"
        title="刷新最近运行"
      >
        <RefreshCw size={16} className={isRefreshing ? "animate-spin" : ""} />
      </button>
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
            <button type="button" className="btn-primary" onClick={() => void loadRuns(true)}>
              重试
            </button>
          }
        />
      </div>
    );
  }

  return (
    <div
      data-run-monitor
      data-frontend-governance-state="ready"
      className={workbenchSurface.page}
    >
      <PanelHeader
        title="运行监控"
        subtitle="按任务、状态、执行动作和失败原因查看最近运行"
        icon={<Activity size={20} />}
        actions={headerActions}
        searchValue={searchQuery}
        onSearchChange={setSearchQuery}
        searchPlaceholder="搜索任务 / Agent / 用户 / 运行编号"
        searchAccessory={
          <span className="hidden shrink-0 text-xs text-[var(--theme-text-tertiary)] sm:inline">
            {autoRefresh ? "每 5 秒自动刷新" : "自动刷新已暂停"} · {lastUpdatedLabel}
          </span>
        }
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

      <section
        aria-label="Worker 运行摘要"
        className="grid grid-cols-2 gap-2 px-4 pb-2 pt-3 xl:grid-cols-4"
      >
        <MetricTile
          icon={<RadioTower size={17} />}
          value={numericValue(queueInsight?.workers?.active)}
          label="Worker 在线"
          detail={queueReasonLabel(queueInsight?.reason)}
        />
        <MetricTile
          icon={<Clock3 size={17} />}
          value={summary.queued}
          label="正在排队"
          detail={`企业队列 ${numericValue(queueInsight?.depths?.tenant_queued)}`}
        />
        <MetricTile
          icon={<ServerCog size={17} />}
          value={summary.running}
          label="正在执行"
          detail={`可用槽位 ${numericValue(queueInsight?.capacity?.available_worker_slots)}`}
        />
        <MetricTile
          icon={<AlertTriangle size={17} />}
          value={summary.failed}
          label="最近失败"
          detail={`最近 ${runs.length} 条运行`}
        />
      </section>

      <div className="flex flex-wrap items-center justify-between gap-2 px-4 pb-3 pt-1">
        <div
          className="inline-flex max-w-full overflow-x-auto rounded-md bg-[var(--theme-bg-sidebar)] p-1 ring-1 ring-[var(--theme-border)]"
          role="group"
          aria-label="运行状态筛选"
        >
          {STATUS_FILTERS.map((filter) => (
            <button
              key={filter.value}
              type="button"
              aria-pressed={statusFilter === filter.value}
              onClick={() => setStatusFilter(filter.value)}
              className="h-7 whitespace-nowrap rounded px-2.5 text-xs font-medium text-[var(--theme-text-secondary)] transition-colors aria-pressed:bg-[var(--theme-workbench-panel)] aria-pressed:text-[var(--theme-text)] aria-pressed:shadow-sm"
            >
              {filter.label}
            </button>
          ))}
        </div>
        <p className="text-xs text-[var(--theme-text-tertiary)]" aria-live="polite">
          最近 {runs.length} 条 · 筛选后 {filteredRuns.length} 条
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

      <div className="relative min-h-0 flex-1 overflow-hidden px-4 pb-4">
        <section
          aria-label="最近 Worker 请求"
          className="h-full min-w-0 overflow-x-auto rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] shadow-[0_1px_2px_rgba(18,38,63,0.04)] xl:overflow-y-auto"
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
            <button
              type="button"
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
          className="flex items-center justify-between gap-3 px-4 pb-4 pt-2 text-xs text-[var(--theme-text-tertiary)]"
        >
          <span className="tabular-nums">
            第 {currentPage} / {pageCount} 页 · 显示 {Math.min((currentPage - 1) * PAGE_SIZE + 1, filteredRuns.length)}-
            {Math.min(currentPage * PAGE_SIZE, filteredRuns.length)} / {filteredRuns.length} 条
          </span>
          <div className="flex shrink-0 items-center gap-1">
            <button
              type="button"
              className="btn-secondary inline-flex h-8 items-center gap-1 rounded-md px-2.5"
              onClick={() => setPage((current) => Math.max(1, current - 1))}
              disabled={currentPage === 1}
              aria-label="上一页"
            >
              <ChevronLeft size={14} />
              <span>上一页</span>
            </button>
            <button
              type="button"
              className="btn-secondary inline-flex h-8 items-center gap-1 rounded-md px-2.5"
              onClick={() => setPage((current) => Math.min(pageCount, current + 1))}
              disabled={currentPage === pageCount}
              aria-label="下一页"
            >
              <span>下一页</span>
              <ChevronRight size={14} />
            </button>
          </div>
        </nav>
      ) : null}

      <span className="sr-only" aria-live="polite">
        {isRefreshing ? "正在刷新运行状态" : `运行状态已更新，${lastUpdatedLabel}`}
      </span>
    </div>
  );
}
