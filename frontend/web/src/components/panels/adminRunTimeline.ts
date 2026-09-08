import type { AdminRunEvent, AdminRunSummary } from "../../services/api/adminRuns";

export type AdminRunTimelineKind = "activity" | "tool" | "terminal";
export type AdminRunTimelineStatus = "info" | "running" | "succeeded" | "failed" | "denied" | "cancelled";

export interface AdminRunTimelineItem {
  id: string;
  kind: AdminRunTimelineKind;
  status: AdminRunTimelineStatus;
  label: string;
  detail: string | null;
  created_at: string | null;
  count: number;
}

export interface AdminRunMonitorView {
  currentStatus: string;
  currentAction: string;
  recentActivity: AdminRunTimelineItem[];
  modelOutput: string;
  rawEventCount: number;
}

const HEARTBEAT_TYPES = new Set(["heartbeat", "stream.heartbeat"]);
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
  run_started: "开始执行",
  worker_started: "Worker 已开始执行",
  runtime_container_started: "运行环境已启动",
  sandbox_executor_readiness_failed: "运行环境未就绪",
  cancel_requested: "已请求取消",
  cancel_requested_but_completed: "取消请求到达时运行已完成",
  context_snapshot_created: "已准备上下文",
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

function eventType(event: AdminRunEvent): string {
  return event.type ?? "event";
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
    created_at: event.created_at ?? item.created_at,
    count: item.count + 1,
  };
}

export function buildAdminRunMonitorView(
  run: AdminRunSummary,
  events: AdminRunEvent[],
): AdminRunMonitorView {
  const modelOutput = run.model_output ?? "";
  const timeline: AdminRunTimelineItem[] = [];
  const toolItems = new Map<string, AdminRunTimelineItem>();
  let legacyToolIdentity: string | null = null;
  let legacyToolNumber = 0;
  let lastProgressKey: string | null = null;
  let latestAction: string | null = null;

  events.forEach((event, index) => {
    const type = eventType(event);
    if (HEARTBEAT_TYPES.has(type)) return;

    if (MODEL_OUTPUT_TYPES.has(type)) {
      latestAction = "模型正在输出";
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
      const key = type;
      const last = timeline.at(-1);
      if (last?.kind === "activity" && lastProgressKey === key) {
        timeline[timeline.length - 1] = mergeProgress(last, event);
        latestAction = last.label;
      } else {
        const item: AdminRunTimelineItem = {
          id: `activity:${event.event_id ?? index}`,
          kind: "activity",
          status: "running",
          label: EVENT_LABELS[type] ?? "正在处理",
          detail: null,
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
        detail: event.error_code ?? null,
        created_at: event.created_at ?? null,
        count: 1,
      });
      latestAction = timeline.at(-1)?.label ?? null;
      return;
    }

    timeline.push({
      id: `activity:${event.event_id ?? index}`,
      kind: "activity",
      status: event.severity === "error" ? "failed" : "info",
      label: EVENT_LABELS[type] ?? "活动更新",
      detail: event.error_code ?? null,
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
    modelOutput,
    rawEventCount: events.length,
  };
}
