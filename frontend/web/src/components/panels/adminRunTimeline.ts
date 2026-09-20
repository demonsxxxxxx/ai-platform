import type {
  AdminRunDiagnosticsResponse,
  AdminRunEvent,
  AdminRunSummary,
} from "../../services/api/adminRuns";

export type AdminRunTimelineKind = "activity" | "tool" | "terminal";
export type AdminRunTimelineStatus = "info" | "running" | "succeeded" | "failed" | "denied" | "cancelled";

export interface AdminRunTimelineItem {
  id: string;
  kind: AdminRunTimelineKind;
  status: AdminRunTimelineStatus;
  label: string;
  detail: string | null;
  stage: string | null;
  duration_ms: number | null;
  created_at: string | null;
  count: number;
}

export interface AdminRunEventDiagnostic {
  id: string;
  sequence: number | null;
  type: string;
  messageId: string | null;
  streamIncarnation: number | null;
  deltaLength: number | null;
  textLength: number | null;
  deltaCount: number | null;
  severity: string | null;
  errorCode: string | null;
  created_at: string | null;
}

export interface AdminRunMonitorView {
  currentStatus: string;
  currentAction: string;
  recentActivity: AdminRunTimelineItem[];
  eventDiagnostics: AdminRunEventDiagnostic[];
  modelOutput: string;
  rawEventCount: number;
}

const HEARTBEAT_TYPES = new Set(["heartbeat", "stream.heartbeat"]);
const NOISY_EVENT_TYPES = new Set([
  "sandbox_lease_renewed",
  "run_control_operation_committed",
]);
const QUEUE_EVENT_TYPES = new Set(["queued", "run_queued"]);
const MODEL_OUTPUT_TYPES = new Set([
  "assistant_delta",
  "message.delta",
  "message.completed",
]);
const PROGRESS_TYPES = new Set(["agent.progress", "agent_progress", "progress"]);
const TOOL_START_TYPES = new Set([
  "mcp_tool_call_started",
  "tool_call_started",
  "tool.started",
]);
const TOOL_DELTA_TYPES = new Set(["mcp_tool_call_delta", "tool_call_delta", "tool.delta"]);
const TOOL_SUCCESS_TYPES = new Set([
  "mcp_tool_call_completed",
  "tool_call_completed",
  "tool.completed",
]);
const TOOL_FAILURE_TYPES = new Set(["tool.failed", "tool_call_failed", "mcp_tool_call_failed"]);
const TOOL_DENIAL_TYPES = new Set([
  "mcp_tool_denied",
  "tool_denied",
  "tool.denied",
  "tool_permission_denied",
  "policy.denied",
]);
const TERMINAL_TYPES = new Set([
  "run_succeeded",
  "run_completed",
  "run_failed",
  "run_cancelled",
  "run.cancelled",
  "run.succeeded",
  "run.failed",
  "error",
]);

const EVENT_LABELS: Record<string, string> = {
  run_queued: "已进入队列",
  queued: "已进入队列",
  run_created: "已创建运行",
  intent_detected: "已识别请求",
  intent_confirmed: "已确认处理方式",
  skill_selected: "已选择能力",
  skill_release_decision: "已锁定能力版本",
  run_started: "开始执行",
  worker_started: "Worker 已开始执行",
  runtime_container_started: "运行环境已启动",
  sandbox_executor_readiness_failed: "运行环境未就绪",
  cancel_requested: "已请求取消",
  cancel_requested_but_completed: "取消请求到达时运行已完成",
  context_snapshot_created: "已准备执行上下文",
  context_retrieved: "已读取执行上下文",
  file_bound: "已绑定输入文件",
  capability_selected: "已选择能力",
  capability_staged: "已准备能力",
  capability_actually_invoked: "已调用能力",
  capability_invoking: "能力正在执行",
  capability_completed: "能力执行完成",
  capability_failed: "能力执行失败",
  "agent.progress": "Agent 进度更新",
  agent_progress: "Agent 进度更新",
  progress: "Agent 进度更新",
  tool_permission_requested: "等待权限决策",
  tool_permission_authorized: "权限已批准",
  tool_permission_denied: "权限被拒绝",
  browser_snapshot: "已读取浏览器状态",
  workspace_file_changed: "工作区文件已更新",
  artifact_created: "已创建产物",
  artifact_ready: "产物已就绪",
  checkpoint_created: "已保存检查点",
  subagent_started: "协同处理已开始",
  subagent_completed: "协同处理已完成",
  subagent_failed: "协同处理失败",
  agent_step_started: "执行步骤已开始",
  agent_step_reused: "已复用执行步骤",
  agent_step_completed: "执行步骤已完成",
  agent_step_blocked: "执行步骤被阻塞",
  agent_step_failed: "执行步骤失败",
  run_succeeded: "运行成功",
  run_completed: "运行完成",
  run_failed: "运行失败",
  run_cancelled: "运行已取消",
  "run.succeeded": "运行成功",
  "run.failed": "运行失败",
  "run.cancelled": "运行已取消",
  error: "发生错误",
};

const PHASE_LABELS: Record<string, string> = {
  attachment_materialization: "正在准备附件",
  skill_staging: "正在准备 Skill",
  sandbox_preparation: "正在准备运行环境",
  sandbox_submission: "正在提交执行任务",
  model_wait: "正在等待模型响应",
  artifact_validation: "正在检查产物",
  artifact_recovery: "正在恢复产物",
};

function eventMessage(event: AdminRunEvent): string | null {
  const message = event.message?.trim();
  return message || null;
}

function payloadText(event: AdminRunEvent, key: string): string | null {
  const value = event.payload?.[key];
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function eventDetail(event: AdminRunEvent): string | null {
  const message = eventMessage(event);
  const queuePosition = event.payload?.queue_position;
  const queueDetail =
    typeof queuePosition === "number" && queuePosition > 0
      ? `当前队列第 ${queuePosition} 位`
      : null;
  const detail = [queueDetail, message, event.error_code ?? null].filter(Boolean);
  return detail.length ? detail.join(" · ") : payloadText(event, "detail");
}

function diagnosticText(value: unknown): string | null {
  if (typeof value === "string" && value.trim()) return value.trim();
  if (Array.isArray(value)) {
    const text = value.find((item) => typeof item === "string" && item.trim());
    return typeof text === "string" ? text.trim() : null;
  }
  return null;
}

function failureDetail(
  run: AdminRunSummary,
  event: AdminRunEvent,
  diagnostics: AdminRunDiagnosticsResponse | null,
): string | null {
  const base = eventDetail(event) ?? run.error_code ?? null;
  if (!diagnostics || event.error_code !== "executor_failure") return base;
  const reason =
    diagnosticText(diagnostics.root?.message) ??
    diagnosticText(diagnostics.details.sdk.exception_message) ??
    diagnosticText(diagnostics.details.sdk.errors) ??
    diagnosticText(diagnostics.root?.source) ??
    diagnosticText(diagnostics.root?.stage);
  if (!reason || base?.includes(reason)) return base;
  return `${base ?? "执行器失败"} · 原因：${reason}`;
}

function eventType(event: AdminRunEvent): string {
  return event.type ?? "event";
}

function diagnosticNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function eventDuration(event: AdminRunEvent): number | null {
  return (
    diagnosticNumber(event.payload?.duration_ms) ??
    diagnosticNumber(event.latency_ms)
  );
}

function eventStage(event: AdminRunEvent): string | null {
  return event.stage?.trim() || payloadText(event, "phase");
}

function progressLabel(event: AdminRunEvent): string {
  const phase = payloadText(event, "phase");
  return (phase && PHASE_LABELS[phase]) || eventMessage(event) || "正在处理";
}

function diagnosticMetadata(event: AdminRunEvent): Record<string, unknown> | null {
  const value = event.payload?.__stream_v4;
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function diagnosticString(
  event: AdminRunEvent,
  key: string,
  metadata: Record<string, unknown> | null,
): string | null {
  const value = metadata?.[key] ?? event.payload?.[key];
  return typeof value === "string" && value.trim() ? value : null;
}

export function buildAdminRunEventDiagnostics(
  events: AdminRunEvent[],
): AdminRunEventDiagnostic[] {
  return events
    .map((event, index) => {
      const type = eventType(event);
      if (!MODEL_OUTPUT_TYPES.has(type) && !TERMINAL_TYPES.has(type)) return null;
      const metadata = diagnosticMetadata(event);
      const delta = event.payload?.delta;
      const content = event.payload?.content;
      return {
        id: event.event_id ?? `event-${index}`,
        sequence: typeof event.sequence === "number" ? event.sequence : null,
        type,
        messageId: diagnosticString(event, "message_id", metadata),
        streamIncarnation: diagnosticNumber(
          metadata?.stream_incarnation ?? event.payload?.stream_incarnation,
        ),
        deltaLength: typeof delta === "string" ? Array.from(delta).length : null,
        textLength:
          diagnosticNumber(event.payload?.text_length) ??
          (typeof content === "string" ? Array.from(content).length : null),
        deltaCount: diagnosticNumber(event.payload?.delta_count),
        severity: event.severity ?? null,
        errorCode: event.error_code ?? null,
        created_at: event.created_at ?? null,
      };
    })
    .filter((item): item is AdminRunEventDiagnostic => item !== null);
}

function displayName(event: AdminRunEvent): string | null {
  const value = event.payload?.display_name;
  return typeof value === "string" && value.trim() ? value : null;
}

function toolIdentity(event: AdminRunEvent): string | null {
  const payload = event.payload;
  if (payload && typeof payload === "object") {
    for (const key of ["operation_id", "tool_call_id", "tool_use_id"]) {
      const value = payload[key];
      if (typeof value === "string" && value) return value;
    }
  }
  return null;
}

function toolLabel(event: AdminRunEvent): string {
  return displayName(event) ?? "工具调用";
}

function toolDetail(type: string): string {
  if (TOOL_FAILURE_TYPES.has(type)) return "执行失败";
  if (TOOL_DENIAL_TYPES.has(type)) return "未获授权";
  if (TOOL_SUCCESS_TYPES.has(type)) return "执行完成";
  return "正在执行";
}

function toolStatus(type: string): AdminRunTimelineStatus {
  if (TOOL_FAILURE_TYPES.has(type)) return "failed";
  if (TOOL_DENIAL_TYPES.has(type)) return "denied";
  if (TOOL_SUCCESS_TYPES.has(type)) return "succeeded";
  return "running";
}

function terminalStatus(type: string): AdminRunTimelineStatus {
  if (type === "run_succeeded" || type === "run_completed" || type === "run.succeeded") {
    return "succeeded";
  }
  if (type === "run_cancelled" || type === "run.cancelled") return "cancelled";
  return "failed";
}

function statusFallback(status: string): string {
  return (
    {
      queued: "等待 Worker 接单",
      running: "正在执行",
      succeeded: "运行成功",
      failed: "运行失败",
      cancelled: "运行已取消",
      cancel_requested: "正在取消",
    }[status] ?? "等待运行状态"
  );
}

function mergeProgress(
  item: AdminRunTimelineItem,
  event: AdminRunEvent,
): AdminRunTimelineItem {
  return {
    ...item,
    label: progressLabel(event),
    detail:
      eventMessage(event) === progressLabel(event) ? null : eventMessage(event),
    stage: eventStage(event) ?? item.stage,
    duration_ms: eventDuration(event) ?? item.duration_ms,
    created_at: event.created_at ?? item.created_at,
    count: item.count + 1,
  };
}

export function buildAdminRunMonitorView(
  run: AdminRunSummary,
  events: AdminRunEvent[],
  diagnostics: AdminRunDiagnosticsResponse | null = null,
): AdminRunMonitorView {
  const modelOutput = run.model_output ?? "";
  const timeline: AdminRunTimelineItem[] = [];
  const toolItems = new Map<string, AdminRunTimelineItem>();
  let legacyToolIdentity: string | null = null;
  let legacyToolNumber = 0;
  let lastProgressKey: string | null = null;
  let latestAction: string | null = null;
  const appendActivity = (item: AdminRunTimelineItem) => {
    const previous = timeline.at(-1);
    if (
      previous?.kind === "activity" &&
      previous.label === item.label &&
      previous.detail === item.detail
    ) {
      timeline[timeline.length - 1] = {
        ...previous,
        stage: item.stage ?? previous.stage,
        duration_ms: item.duration_ms ?? previous.duration_ms,
        created_at: item.created_at ?? previous.created_at,
        count: previous.count + item.count,
      };
      return;
    }
    timeline.push(item);
  };

  events.forEach((event, index) => {
    const type = eventType(event);
    if (HEARTBEAT_TYPES.has(type) || NOISY_EVENT_TYPES.has(type)) return;

    if (QUEUE_EVENT_TYPES.has(type)) {
      const queueItem: AdminRunTimelineItem = {
        id: "activity:queue",
        kind: "activity",
        status: "info",
        label: EVENT_LABELS[type] ?? "已进入队列",
        detail: eventDetail(event),
        stage: eventStage(event) ?? "queue",
        duration_ms: eventDuration(event),
        created_at: event.created_at ?? null,
        count: 1,
      };
      const existingIndex = timeline.findIndex((item) => item.id === queueItem.id);
      if (existingIndex >= 0) timeline.splice(existingIndex, 1);
      timeline.push(queueItem);
      latestAction = queueItem.label;
      lastProgressKey = null;
      return;
    }

    if (MODEL_OUTPUT_TYPES.has(type)) {
      const key = "activity:model-output";
      const existingIndex = timeline.findIndex((item) => item.id === key);
      const completed = type === "message.completed";
      const item: AdminRunTimelineItem = {
        id: key,
        kind: "activity",
        status: completed ? "succeeded" : "running",
        label: completed ? "模型生成完成" : "模型正在输出",
        detail: null,
        stage: eventStage(event) ?? "model",
        duration_ms: eventDuration(event),
        created_at: event.created_at ?? null,
        count: existingIndex >= 0 ? timeline[existingIndex].count + 1 : 1,
      };
      if (existingIndex >= 0) timeline[existingIndex] = item;
      else timeline.push(item);
      latestAction = item.label;
      return;
    }

    if (
      TOOL_START_TYPES.has(type) ||
      TOOL_DELTA_TYPES.has(type) ||
      TOOL_SUCCESS_TYPES.has(type) ||
      TOOL_FAILURE_TYPES.has(type) ||
      TOOL_DENIAL_TYPES.has(type)
    ) {
      const explicitIdentity = toolIdentity(event);
      const identity = explicitIdentity
        ? explicitIdentity
        : TOOL_START_TYPES.has(type)
          ? `legacy-${++legacyToolNumber}`
          : legacyToolIdentity ?? `legacy-${index}`;
      const key = `tool:${identity}`;
      const existing = toolItems.get(key);
      if (existing) {
        const updated = {
          ...existing,
          status: toolStatus(type),
          label: displayName(event) ?? existing.label,
          detail: toolDetail(type),
          stage: eventStage(event) ?? existing.stage,
          duration_ms: eventDuration(event) ?? existing.duration_ms,
          created_at: event.created_at ?? existing.created_at,
          count: existing.count + 1,
        };
        toolItems.set(key, updated);
        const position = timeline.findIndex((item) => item.id === key);
        if (position >= 0) timeline[position] = updated;
        latestAction = updated.label;
      } else {
        const item: AdminRunTimelineItem = {
          id: key,
          kind: "tool",
          status: toolStatus(type),
          label: toolLabel(event),
          detail: toolDetail(type),
          stage: eventStage(event) ?? "tool",
          duration_ms: eventDuration(event),
          created_at: event.created_at ?? null,
          count: 1,
        };
        toolItems.set(key, item);
        timeline.push(item);
        latestAction = item.label;
      }
      if (!explicitIdentity && TOOL_START_TYPES.has(type)) {
        legacyToolIdentity = identity;
      } else if (
        !explicitIdentity &&
        (TOOL_SUCCESS_TYPES.has(type) ||
          TOOL_FAILURE_TYPES.has(type) ||
          TOOL_DENIAL_TYPES.has(type))
      ) {
        legacyToolIdentity = null;
      }
      lastProgressKey = null;
      return;
    }

    if (PROGRESS_TYPES.has(type)) {
      const key = `${type}:${payloadText(event, "phase") ?? "generic"}`;
      const last = timeline.at(-1);
      if (last?.kind === "activity" && lastProgressKey === key) {
        const merged = mergeProgress(last, event);
        timeline[timeline.length - 1] = merged;
        latestAction = merged.label;
      } else {
        const item: AdminRunTimelineItem = {
          id: `activity:${event.event_id ?? index}`,
          kind: "activity",
          status: "running",
          label: progressLabel(event),
          detail: eventMessage(event) === progressLabel(event) ? null : eventMessage(event),
          stage: eventStage(event),
          duration_ms: eventDuration(event),
          created_at: event.created_at ?? null,
          count: 1,
        };
        timeline.push(item);
        latestAction = item.label;
      }
      lastProgressKey = key;
      return;
    }

    lastProgressKey = null;
    if (TERMINAL_TYPES.has(type)) {
      timeline.push({
        id: `terminal:${event.event_id ?? index}`,
        kind: "terminal",
        status: terminalStatus(type),
        label: EVENT_LABELS[type] ?? "运行已结束",
        detail: failureDetail(
          run,
          {
            ...event,
            error_code: event.error_code ?? run.error_code,
          },
          diagnostics,
        ),
        stage: eventStage(event) ?? "terminal",
        duration_ms: eventDuration(event),
        created_at: event.created_at ?? null,
        count: 1,
      });
      latestAction = timeline.at(-1)?.label ?? null;
      return;
    }

    const label = EVENT_LABELS[type] ?? eventMessage(event) ?? "活动更新";
    appendActivity({
      id: `activity:${event.event_id ?? index}`,
      kind: "activity",
      status: event.severity === "error" ? "failed" : "info",
      label,
      detail: eventMessage(event) === label ? event.error_code ?? null : eventDetail(event),
      stage: eventStage(event),
      duration_ms: eventDuration(event),
      created_at: event.created_at ?? null,
      count: 1,
    });
    latestAction = timeline.at(-1)?.label ?? null;
  });

  const currentAction = latestAction ?? statusFallback(run.status);
  return {
    currentStatus: run.status,
    currentAction,
    recentActivity: timeline,
    eventDiagnostics: buildAdminRunEventDiagnostics(events),
    modelOutput,
    rawEventCount: events.length,
  };
}
