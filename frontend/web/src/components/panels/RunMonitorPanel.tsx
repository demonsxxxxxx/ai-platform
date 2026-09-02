import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  CircleX,
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
  type AdminQueueInsight,
  type AdminRunDetailResponse,
  type AdminRunSummary,
} from "../../services/api/adminRuns";
import { formatDateTimeShort } from "../../utils/datetime";

const POLL_INTERVAL_MS = 5_000;
const RUN_LIMIT = 50;

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
  return (
    <div className="min-w-0">
      <dt className="text-[11px] font-medium text-[var(--theme-text-tertiary)]">
        {label}
      </dt>
      <dd className="mt-1 break-all font-mono text-xs leading-5 text-[var(--theme-text)]">
        {value || "-"}
      </dd>
    </div>
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
  loading,
  error,
  onClose,
  fallbackFocusRef,
}: {
  detail: AdminRunDetailResponse | null;
  loading: boolean;
  error: string | null;
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

  return (
    <aside
      ref={detailRef}
      data-run-monitor-detail
      role="dialog"
      aria-modal="true"
      className="h-full min-w-0 overflow-y-auto rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] shadow-[0_8px_24px_rgba(18,38,63,0.12)]"
      aria-label="运行详情"
    >
      <div className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-4 py-3">
        <div className="min-w-0">
          <p className="text-xs font-medium text-[var(--theme-text-secondary)]">
            Worker 请求详情
          </p>
          <h2 className="truncate font-mono text-sm font-semibold text-[var(--theme-text)]">
            {detail?.run.run_id ?? "正在读取"}
          </h2>
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
          <section className="p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-xs font-semibold text-[var(--theme-text)]">
                请求身份
              </h3>
              <StatusBadge status={detail.run.status} />
            </div>
            <dl className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
              <IdentityField label="Chat / Session ID" value={detail.run.session_id} />
              <IdentityField label="Trace ID" value={detail.run.trace_id} />
              <IdentityField label="用户 ID" value={detail.run.user_id} />
              <IdentityField label="工作区" value={detail.run.workspace_id} />
              <IdentityField label="专家" value={detail.run.agent_id} />
              <IdentityField label="Skill" value={detail.run.skill_id} />
            </dl>
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
                  {durationLabel(detail.run)}
                </p>
              </div>
            </div>
            {detail.run.error_code ? (
              <div className="mt-3 border-l-2 border-l-[var(--theme-danger)] bg-[var(--theme-danger-soft)] px-3 py-2 text-xs text-[var(--theme-danger)]">
                <p className="font-mono font-medium">{detail.run.error_code}</p>
                {detail.run.error_message ? (
                  <p className="mt-1 leading-5">{detail.run.error_message}</p>
                ) : null}
              </div>
            ) : null}
          </section>

          <section className="p-4">
            <h3 className="text-xs font-semibold text-[var(--theme-text)]">
              阶段事件 <span className="font-normal text-[var(--theme-text-tertiary)]">({detail.events.length})</span>
            </h3>
            {detail.events.length ? (
              <ol className="mt-3 space-y-3">
                {detail.events.map((event, index) => (
                  <li
                    key={event.event_id ?? `${event.type ?? "event"}-${index}`}
                    className="grid grid-cols-[10px_minmax(0,1fr)] gap-3"
                  >
                    <span className="mt-1.5 size-2 rounded-full bg-[var(--theme-info)] ring-2 ring-[var(--theme-info-soft)]" />
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <span className="font-mono text-xs font-medium text-[var(--theme-text)]">
                          {event.type ?? "event"}
                        </span>
                        {event.stage ? (
                          <span className="text-[11px] text-[var(--theme-text-tertiary)]">
                            {event.stage}
                          </span>
                        ) : null}
                        <time className="ml-auto text-[11px] text-[var(--theme-text-tertiary)]">
                          {dateTime(event.created_at)}
                        </time>
                      </div>
                      {event.message ? (
                        <p className="mt-1 text-xs leading-5 text-[var(--theme-text-secondary)]">
                          {event.message}
                        </p>
                      ) : null}
                    </div>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="mt-2 text-xs text-[var(--theme-text-tertiary)]">
                暂无阶段事件
              </p>
            )}
          </section>

          <section className="p-4">
            <h3 className="text-xs font-semibold text-[var(--theme-text)]">
              执行步骤 <span className="font-normal text-[var(--theme-text-tertiary)]">({detail.steps.length})</span>
            </h3>
            {detail.steps.length ? (
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
            ) : (
              <p className="mt-2 text-xs text-[var(--theme-text-tertiary)]">
                暂无执行步骤
              </p>
            )}
          </section>

          <section className="p-4">
            <h3 className="text-xs font-semibold text-[var(--theme-text)]">
              沙箱租约 <span className="font-normal text-[var(--theme-text-tertiary)]">({detail.sandbox_leases.length})</span>
            </h3>
            {detail.sandbox_leases.length ? (
              <div className="mt-3 space-y-2">
                {detail.sandbox_leases.map((lease, index) => (
                  <div
                    key={lease.lease_id ?? lease.id ?? `lease-${index}`}
                    className="rounded-md border border-[var(--theme-border)] p-2.5"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-mono text-xs text-[var(--theme-text)]">
                        {lease.lease_id ?? lease.id ?? "lease"}
                      </span>
                      <StatusBadge status={lease.status} />
                    </div>
                    <p className="mt-1.5 text-[11px] text-[var(--theme-text-tertiary)]">
                      {[lease.provider, lease.sandbox_mode].filter(Boolean).join(" · ") || "平台沙箱"}
                    </p>
                  </div>
                ))}
              </div>
            ) : (
              <p className="mt-2 text-xs text-[var(--theme-text-tertiary)]">
                此运行没有沙箱租约
              </p>
            )}
          </section>
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
            <th className="border-b border-[var(--theme-border)] px-3 py-2.5 font-medium">Chat / Run</th>
            <th className="border-b border-[var(--theme-border)] px-3 py-2.5 font-medium">用户 / 工作区</th>
            <th className="border-b border-[var(--theme-border)] px-3 py-2.5 font-medium">专家 / Skill</th>
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
                    <span className="block truncate font-mono font-medium text-[var(--theme-text)]" title={run.session_id ?? ""}>
                      {run.session_id ?? "无 Session"}
                    </span>
                    <span className="mt-1 block truncate font-mono text-[11px] text-[var(--theme-text-tertiary)]" title={run.run_id}>
                      {run.run_id}
                    </span>
                  </button>
                </td>
                <td className="max-w-[190px] border-b border-[var(--theme-border)] px-3 py-3 align-top">
                  <p className="truncate text-[var(--theme-text)]" title={run.user_id}>{run.user_id}</p>
                  <p className="mt-1 truncate text-[11px] text-[var(--theme-text-tertiary)]" title={run.workspace_id ?? ""}>{run.workspace_id ?? "default"}</p>
                </td>
                <td className="max-w-[210px] border-b border-[var(--theme-border)] px-3 py-3 align-top">
                  <p className="truncate text-[var(--theme-text)]" title={run.agent_id ?? ""}>{run.agent_id ?? "-"}</p>
                  <p className="mt-1 truncate text-[11px] text-[var(--theme-text-tertiary)]" title={run.skill_id ?? ""}>{run.skill_id ?? "-"}</p>
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
              <p className="truncate font-mono text-xs font-medium text-[var(--theme-text)]">
                {run.session_id ?? "无 Session"}
              </p>
              <p className="mt-1 truncate font-mono text-[11px] text-[var(--theme-text-tertiary)]">
                {run.run_id}
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
            <span className="truncate">{run.user_id} · {run.agent_id ?? "-"}</span>
            <span className="shrink-0 tabular-nums">{durationLabel(run)}</span>
          </div>
        </button>
      ))}
    </div>
  );
}

export function RunMonitorPanel() {
  const [runs, setRuns] = useState<AdminRunSummary[]>([]);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [detail, setDetail] = useState<AdminRunDetailResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null);
  const listRequestSequence = useRef(0);
  const detailRequestSequence = useRef(0);
  const selectedRunIdRef = useRef<string | null>(null);
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

  const loadRuns = useCallback(async (initial = false) => {
    const requestId = ++listRequestSequence.current;
    if (initial) setIsLoading(true);
    else setIsRefreshing(true);
    try {
      const response = await adminRunsApi.list(RUN_LIMIT);
      if (requestId !== listRequestSequence.current) return;
      setRuns(response.runs ?? []);
      setLoadError(null);
      setLastUpdatedAt(new Date());
      const activeRunId = selectedRunIdRef.current;
      if (activeRunId) void loadDetail(activeRunId, false);
    } catch (error) {
      if (requestId !== listRequestSequence.current) return;
      setLoadError(error instanceof Error ? error.message : "最近运行加载失败");
    } finally {
      if (requestId === listRequestSequence.current) {
        setIsLoading(false);
        setIsRefreshing(false);
      }
    }
  }, [loadDetail]);

  useEffect(() => {
    void loadRuns(true);
  }, [loadRuns]);

  useEffect(() => {
    if (!autoRefresh) return;
    const interval = window.setInterval(() => {
      if (document.visibilityState === "visible") void loadRuns(false);
    }, POLL_INTERVAL_MS);
    const handleVisibility = () => {
      if (document.visibilityState === "visible") void loadRuns(false);
    };
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [autoRefresh, loadRuns]);

  const selectRun = useCallback((runId: string) => {
    selectedRunIdRef.current = runId;
    setSelectedRunId(runId);
    setDetail(null);
    void loadDetail(runId);
  }, [loadDetail]);

  const closeDetail = useCallback(() => {
    detailRequestSequence.current += 1;
    selectedRunIdRef.current = null;
    setSelectedRunId(null);
    setDetail(null);
    setDetailError(null);
    setDetailLoading(false);
  }, []);

  const filteredRuns = useMemo(
    () => filterAdminRuns(runs, statusFilter, searchQuery),
    [runs, searchQuery, statusFilter],
  );
  const summary = useMemo(() => summarizeAdminRuns(runs), [runs]);
  const queueInsight = useMemo(() => latestQueueInsight(runs), [runs]);
  const lastUpdatedLabel = lastUpdatedAt
    ? lastUpdatedAt.toLocaleTimeString("zh-CN", { hour12: false })
    : "尚未刷新";

  const headerActions = (
    <>
      <button
        type="button"
        className="btn-icon flex size-9 items-center justify-center rounded-md"
        onClick={() => setAutoRefresh((current) => !current)}
        aria-pressed={autoRefresh}
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
    </>
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
        subtitle="查看最近的 Chat、Run 与 Worker 生命周期状态"
        icon={<Activity size={20} />}
        actions={headerActions}
        searchValue={searchQuery}
        onSearchChange={setSearchQuery}
        searchPlaceholder="搜索 Chat / Run / 用户 / 工作区"
        searchAccessory={
          <span className="hidden shrink-0 text-xs text-[var(--theme-text-tertiary)] sm:inline">
            {autoRefresh ? "每 5 秒更新" : "自动刷新已暂停"} · {lastUpdatedLabel}
          </span>
        }
      />

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
          显示 {filteredRuns.length} / {runs.length} 条
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
                runs={filteredRuns}
                selectedRunId={selectedRunId}
                onSelect={selectRun}
              />
              <MobileRunList
                runs={filteredRuns}
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
                  : "新的 Chat 请求入队后会自动出现在这里。"}
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
            <div className="absolute inset-y-0 right-0 w-full xl:w-[420px] xl:p-2">
              <RunDetail
                detail={detail}
                loading={detailLoading}
                error={detailError}
                onClose={closeDetail}
                fallbackFocusRef={refreshButtonRef}
              />
            </div>
          </div>
        ) : null}
      </div>

      <span className="sr-only" aria-live="polite">
        {isRefreshing ? "正在刷新运行状态" : `运行状态已更新，${lastUpdatedLabel}`}
      </span>
    </div>
  );
}
