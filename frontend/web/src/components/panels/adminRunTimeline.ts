import type {
  AdminRunDiagnosticToolEvidence,
  AdminRunEvent,
  AdminRunSummary,
} from "../../services/api/adminRuns";

export interface AdminRunMonitorView {
  currentAction: string;
  modelOutput: string;
}

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
  tool_permission_denied: "权限被拒绝",
  browser_snapshot: "已读取浏览器状态",
  workspace_file_changed: "工作区文件已更新",
  artifact_created: "已创建产物",
  artifact_ready: "产物已就绪",
  checkpoint_created: "已保存检查点",
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

export function buildAdminRunMonitorView(run: AdminRunSummary, events: AdminRunEvent[]): AdminRunMonitorView {
  const fallback = {
    queued: "等待 Worker 接单", running: "正在执行", succeeded: "运行成功",
    failed: "运行失败", cancelled: "运行已取消", cancel_requested: "正在取消",
  }[run.status] ?? "等待运行状态";
  let currentAction = fallback;
  if (run.status === "running") {
    for (const event of [...events].reverse()) {
      const type = event.type ?? "event";
      if (event.visible_to_user === false || type.includes("heartbeat")
        || event.payload?.heartbeat === true || ["sandbox_lease_renewed", "run_control_operation_committed"].includes(type)) continue;
      if (["agent.progress", "agent_progress", "progress"].includes(type)) {
        const phase = event.payload?.phase;
        currentAction = typeof phase === "string" && PHASE_LABELS[phase] || event.message?.trim() || "正在处理";
      } else if (type.startsWith("tool.")) {
        const name = event.payload?.display_name;
        currentAction = typeof name === "string" && name.trim() ? name : "工具调用";
      } else if (["assistant_delta", "message.delta", "message.part.delta", "message.completed"].includes(type)) {
        currentAction = type === "message.completed" ? "公开回答已生成，等待任务收尾" : "正在生成公开回答";
      } else {
        const label = EVENT_LABELS[type];
        if (!label) continue;
        currentAction = label;
      }
      break;
    }
  }
  return { currentAction, modelOutput: run.model_output ?? "" };
}

export function indexToolDiagnosticEvidence(evidence: AdminRunDiagnosticToolEvidence[]) {
  const indexed = new Map<string, AdminRunDiagnosticToolEvidence>();
  const conflicts = new Set<string>();
  for (const item of evidence) {
    // Public operations and legacy native-ID actions remain separate aliases.
    for (const key of new Set([item.operation_id, item.invocation_id])) {
      if (!key || !key.trim() || conflicts.has(key)) continue;
      const previous = indexed.get(key);
      if (previous && (previous.tool_name !== item.tool_name
        || previous.invocation_id !== item.invocation_id
        || previous.operation_id !== item.operation_id)) {
        indexed.delete(key);
        conflicts.add(key);
      } else {
        indexed.set(key, item);
      }
    }
  }
  return indexed;
}
