import assert from "node:assert/strict";
import test from "node:test";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { installSelectTestDom } from "../../common/__tests__/selectTestDom";
import { createLobeTestRoot } from "../../common/__tests__/lobeUiTestRoot";
import { act } from "react";

import {
  adminRunsApi,
  readAdminRunDeepLinkScope,
  type AdminRunDiagnosticsResponse,
  type AdminRunDetailResponse,
  type AdminRunSummary,
  type AdminRunMonitorResponse,
  type AdminRunInvestigation,
} from "../../../services/api/adminRuns";
import {
  indexToolDiagnosticEvidence,
  filterAdminRuns,
} from "../adminRunTimeline";
const fixture = installSelectTestDom();
const dom = { window };
test.after(() => fixture.close());
const { RunMonitorPanel } = await import("../RunMonitorPanel");
const { RunDiagnosticsSection } = await import("../RunDiagnosticsSection");

const waitFor = async (predicate: () => boolean, timeoutMs = 2_000) => {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    if (predicate()) return;
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
    });
  }
  throw new Error("condition not met before timeout");
};

const runs: AdminRunSummary[] = [
  {
    run_id: "run_running",
    session_id: "chat_2026_04",
    session_title: "审核季度采购合同",
    task_summary: "请检查采购合同中的付款条款",
    user_id: "user-a",
    user_display_name: "王敏",
    workspace_id: "workspace-a",
    workspace_name: "法务工作区",
    trace_id: "trace-a",
    status: "running",
    agent_id: "agent-review",
    agent_name: "合同审阅助手",
    skill_id: "skill-docx",
    skill_name: "文档审阅",
    created_at: "2026-04-01T09:00:00Z",
    started_at: "2026-04-01T09:00:02Z",
    queue_position: null,
    queue_insight: {
      reason: "workers_busy",
      depths: { tenant_queued: 2, tenant_processing: 1 },
      workers: { active: 4 },
      capacity: { available_worker_slots: 0 },
    },
  },
  {
    run_id: "run_failed",
    session_id: "chat_failed",
    session_title: "排查支付接口故障",
    task_summary: "定位支付接口失败原因",
    user_id: "user-b",
    user_display_name: "李哲",
    workspace_id: "workspace-b",
    workspace_name: "支付平台",
    status: "failed",
    agent_id: "agent-code",
    agent_name: "代码排障助手",
    skill_id: "skill-python",
    skill_name: "代码分析",
    created_at: "2026-04-01T08:00:00Z",
    started_at: "2026-04-01T08:00:01Z",
    finished_at: "2026-04-01T08:00:04Z",
    error_code: "worker_execution_failed",
    error_message: "Worker 请求失败",
  },
];

const paginatedRuns: AdminRunSummary[] = [
  ...runs,
  ...Array.from({ length: 10 }, (_, index) => ({
    ...runs[1],
    run_id: `run_failed_${index}`,
    session_id: `chat_failed_${index}`,
    error_code: `worker_execution_failed_${index}`,
  })),
];

const metric = { sample_count: 2, p50: 2400, p95: 4000, p99: 4100 };
const metrics = { total_ms: metric, queue_ms: metric, execution_ms: metric, server_first_text_ms: metric, browser_first_text_ms: { sample_count: 0, p50: null, p95: null, p99: null } };
function monitorResponse(items: AdminRunSummary[]): AdminRunMonitorResponse {
  return { measurement: "run_lifecycle.v1", window: { from_at: "2026-04-01T00:00:00Z", to_at: "2026-04-02T00:00:00Z" },
    counts: { total: 80, succeeded: 60, failed: 10, cancelled: 2, queued: 3, running: 5 }, success_rate: 60 / 70,
    latency: metrics, models: [], runs: items, limit: 50, has_more: true, order: "slow" };
}
function investigationResponse(runId: string): AdminRunInvestigation {
  return { run_id: runId, total_ms: null, queue_ms: 2000, execution_ms: null, server_first_text_ms: 0, browser_first_text_ms: null,
    elapsed_ms: 4000, first_public_text_at: null, browser_measurement: null, observed_at: "2026-04-01T09:00:04Z",
    timeline_source: "platform_receipts", durations_additive: false, unbound_event_count: 0, events_truncated: false, missing_measurements: ["browser_first_text_ms"],
    stages: [{ stage_id: "tool:attempt-a:op-a", label: "Read", source: "sdk_tool_hooks", status: "denied", attempt_id: "attempt-a",
      operation_id: "op_6aaadf71488c9cf9ca0e4928bbf70f1e", event_id: "tool-event", causation_event_id: "start-event",
      started_at: "2026-04-01T09:00:02Z", finished_at: "2026-04-01T09:00:03Z", offset_ms: 2000, receipt_duration_ms: 1000, duration_ms: 1200 }] };
}

test("Run Monitor filters only the explicitly projected Run identities", () => {
  assert.deepEqual(filterAdminRuns(runs, "running", "chat_2026").map((run) => run.run_id), [
    "run_running",
  ]);
  assert.deepEqual(filterAdminRuns(runs, "failed", "worker_execution_failed").map((run) => run.run_id), [
    "run_failed",
  ]);
  assert.deepEqual(filterAdminRuns([{ ...runs[0], status: "cancel_requested" }], "running", "").map((run) => run.run_id), ["run_running"]);
  assert.deepEqual(filterAdminRuns(runs, "running", "合同审阅助手").map((run) => run.run_id), [
    "run_running",
  ]);
});

test("Run Monitor reads user and Run deep-link scope", () => {
  assert.deepEqual(readAdminRunDeepLinkScope("?user_id=user%2Fa&run_id=run%2Fa"), {
    userId: "user/a",
    runId: "run/a",
  });
});

const exportOwnershipCases = [
  { reopenSameRun: false, staleFails: true },
  { reopenSameRun: false, staleFails: false },
  { reopenSameRun: true, staleFails: true },
  { reopenSameRun: true, staleFails: false },
];
for (const scenario of [
  ...[1, 0, null].map((observedCost) => ({ observedCost, exportOwnership: null })),
  ...exportOwnershipCases.map((exportOwnership) => ({ observedCost: null, exportOwnership })),
]) {
  const { observedCost, exportOwnership } = scenario;
  const name = exportOwnership
    ? `Run Monitor export ownership: ${exportOwnership.reopenSameRun ? "same Run reopened" : "A to B"}, stale ${exportOwnership.staleFails ? "failure" : "success"}`
    : `Run Monitor mounts authorized diagnostics with estimated cost ${observedCost}`;
  test(name, async () => {
  document.body.innerHTML = '<div id="root"></div>';
  window.history.replaceState(null, "", "/runs");
  const originalMonitor = adminRunsApi.monitor;
  const originalInvestigation = adminRunsApi.investigation;
  const originalDetail = adminRunsApi.detail;
  const originalDiagnostics = adminRunsApi.diagnostics;
  const originalTrajectory = adminRunsApi.trajectory;
  const originalExportDiagnostics = adminRunsApi.exportDiagnostics;
  const originalInspection = adminRunsApi.inspection;
  adminRunsApi.inspection = async (runId) => ({ contract_version: "ai-platform.admin-run-inspection.v1", run_id: runId, input: { fields: {}, coverage: "not_collected", excluded: [] }, configuration: { source: "persisted_run_snapshot", model_id: null, model_value: null, model_gateway_revision: null, auto_compact_window: null, max_output_tokens: null, profile_revision: null, profile_hash: null, skills: [], skills_truncated: false }, history: { source_run_id: null, relationships: [], truncated: false }, trigger: {kind: null, coverage: "not_collected"} });
  const calls: string[] = [];

  Object.defineProperty(dom.window.HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: () => undefined,
  });
  Object.defineProperty(dom.window.HTMLAnchorElement.prototype, "click", {
    configurable: true,
    value: () => calls.push("download-click"),
  });

  const detail = {
    run: {
      ...runs[0],
      latency_ms: 2_400,
      input_token_count: 101,
      output_token_count: 37,
      total_token_count: 138,
      estimated_cost_minor: observedCost,
      model_value: "claude-sonnet",
      trace_id_recorded: true,
      input: { prompt: "PRIVATE_PROMPT_MARKER" },
      result: {
        text: "PRIVATE_RESULT_MARKER",
      },
    },
    worker_execution: {
      response: "WORKER_EFFECTIVE_RESPONSE",
      messages: [
        {
          ordinal: 1,
          kind: "commentary",
          text: "正在确认日志来源",
          sequence: 1,
          created_at: "2026-04-01T09:00:01Z",
        },
        {
          ordinal: 2,
          kind: "answer",
          text: "WORKER_EFFECTIVE_RESPONSE",
          sequence: 22,
          created_at: "2026-04-01T09:00:04Z",
        },
      ],
      actions: [
        {
          ordinal: 1,
          sequence: 10,
          invocation_id: "op_6aaadf71488c9cf9ca0e4928bbf70f1e",
          label: "Read",
          category: "read",
          status: "succeeded",
          input_summary: "读取 3 个文件",
          result_summary: "已识别 2 个问题",
          duration_ms: 1200,
          started_at: "2026-04-01T09:00:02Z",
          finished_at: "2026-04-01T09:00:03Z",
        },
      ],
      model: {
        turn_count: 3,
        duration_ms: 2400,
        stop_category: "completed",
      },
    },
    events: [
      ...Array.from({ length: 21 }, (_, index) => ({
        event_id: `event-message-${index}`,
        sequence: index + 1,
        type: "message.delta",
        payload: {
          delta: "DIAGNOSTIC_EVENT_BODY_MARKER",
          __stream_v4: { message_id: "message-a", stream_incarnation: 2 },
        },
      })),
      {
        event_id: "event-a",
        type: "run_started",
        stage: "worker",
        message: "Worker 已领取请求",
        created_at: "2026-04-01T09:00:02Z",
        payload: { command: "PRIVATE_EVENT_PAYLOAD_MARKER" },
      },
    ],
    steps: [
      {
        step_id: "step-a",
        title: null,
        step_kind: "worker_setup",
        status: "succeeded",
        payload: { output: "步骤返回摘要" },
        started_at: "2026-04-01T09:00:02Z",
        finished_at: "2026-04-01T09:00:03Z",
      },
    ],
    artifacts: [
      {
        artifact_id: "artifact-a",
        artifact_type: "document",
        label: "审核结果.docx",
        content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size_bytes: 2048,
        created_at: "2026-04-01T09:00:04Z",
      },
    ],
    sandbox_leases: [
      {
        lease_id: "lease-a",
        status: "active",
        provider: "opensandbox",
        sandbox_mode: "ephemeral",
        lease_payload: {
          runtime_path: "PRIVATE_LEASE_PAYLOAD_MARKER",
          release_reason: "PRIVATE_RELEASE_REASON_MARKER /runtime/secret",
        },
      },
    ],
    runtime_health: {
      state: "healthy", observed_at: "2026-04-01T10:00:04Z", attempt_id: "attempt-b",
      async_dispatch_accepted: true, queue_last_heartbeat_at: "2026-04-01T09:00:04Z",
      queue_lease_expires_at: "2026-04-01T09:15:04Z", heartbeat_source: "executor",
      heartbeat_at: "2026-04-01T10:00:02Z",
    },
    audit: [{ payload: { credential: "PRIVATE_AUDIT_PAYLOAD_MARKER" } }],
    skill_snapshots: [],
  } as AdminRunDetailResponse;
  const diagnostics: AdminRunDiagnosticsResponse = {
    schema_version: "ai-platform.run-diagnostics.v1",
    diagnostic_id: "rdiag-a",
    revision: 2,
    coverage: "partial",
    run: {
      ...runs[0],
      session_id: runs[0].session_id ?? null,
      user_id: runs[0].user_id ?? null,
      workspace_id: runs[0].workspace_id ?? "",
    },
    root: {
      observation_id: "obs-origin",
      attempt_id: "attempt-a",
      kind: "failure",
      source: "sdk_result_error",
      stage: "model_wait",
      error_code: "claude_agent_sdk_tool_admission_failed",
      exception_type: "RuntimeError",
      message: "ACTUAL_SDK_FAILURE_MARKER",
      stack: "model.py:42\nRuntimeError: ACTUAL_STACK_TAIL_MARKER",
    },
    handling: [
      {
        observation_id: "obs-wrapper",
        parent_observation_id: "obs-origin",
        attempt_id: "attempt-a",
        kind: "handling",
        source: "sandbox_terminal_normalization",
        stage: "terminalization",
        error_code: "required_tool_completion_evidence_mismatch",
      },
    ],
    losses: [
      { field: "sdk.errors", reason: "redacted", count: 0 },
      { field: "sdk.stack", reason: "truncated", original_bytes: 8192, retained_bytes: 4096 },
      { field: "runtime_diagnostics", reason: "invalid_payload", count: 1 },
      { field: "sdk.future", reason: "future_reason" },
      ...Array.from({ length: 9 }, (_, index) => ({
      field: `sdk.exception_chain[${index}]`,
      reason: "truncated",
      original: 9,
      retained: 8,
    })),
    ],
    attempts: [
      {
        attempt_id: "attempt-a",
        ordinal: 1,
        status: "failed",
        owner_kind: "queue_worker",
        started_at: "2026-04-01T09:00:02Z",
        finished_at: "2026-04-01T09:00:04Z",
        terminal_reason: "run_failed",
        error_code: "claude_agent_sdk_tool_admission_failed",
      },
      {
        attempt_id: "attempt-b", ordinal: 2, status: "running", owner_kind: "queue_worker",
        started_at: "2026-04-01T09:00:04Z", finished_at: null,
      },
    ],
    details: {
      schema_version: "ai-platform.sdk-runtime-diagnostics.v1",
      sdk: { api_error_status: 429, errors: ["ACTUAL_SDK_FAILURE_MARKER"] },
      observations: [
        {
          observation_id: "obs-a",
          parent_observation_id: "obs-wrapper",
          attempt_id: "attempt-a",
          source: "sdk_result_error",
          stage: "model_wait",
          error_code: "claude_agent_sdk_tool_admission_failed",
          sdk: {
            api_error_status: 429,
            errors: ["ACTUAL_SDK_FAILURE_MARKER"],
            exception_chain: [
              {
                type: "RuntimeError",
                message: "ACTUAL_CHAIN_MARKER",
                relation: "cause",
              },
            ],
          },
          tool_lifecycles: [],
          tool_calls: [
            { tool_name: "Read", invocation_id: "abort-true", last_stage: "failed", is_interrupt: true },
            { tool_name: "Read", invocation_id: "abort-false", last_stage: "failed", is_interrupt: false },
            { tool_name: "Read", invocation_id: "abort-unknown", last_stage: "failed" },
          ],
          tool_policy_denials: [
            {
              tool_name: "Read",
              operation_id: "op_6aaadf71488c9cf9ca0e4928bbf70f1e",
              invocation_id: "tool-call-7",
              reason: "tool_parameters_not_authorized",
            },
          ],
          projection_failure: {
            reason: "raw_frame_invalid",
            stage: "message",
            location: "raw_stream_frame",
            frame_shape: {
              event_type: "content_block_delta", block_type: "other", delta_type: "text_delta",
              message_state: "open", open_block_type: "tool_use", index_state: "ignored",
              guard: "block_delta_type",
            },
          },
          normalization_losses: [],
        },
        {
          observation_id: "obs-b",
          parent_observation_id: "obs-a",
          attempt_id: "attempt-a",
          source: "executor_reconciler",
          stage: "terminalization",
          error_code: "terminal_reconciliation_failed",
          sdk: { errors: ["artifact_manifest_invalid"] },
          tool_lifecycles: [],
          tool_calls: [],
          tool_policy_denials: [],
          normalization_losses: [
            { field: "sdk.errors", reason: "truncated", count: 1 },
          ],
        },
      ],
      tool_lifecycles: [],
      tool_calls: [],
      tool_policy_denials: [
        {
          tool_name: "Read",
          operation_id: "op_6aaadf71488c9cf9ca0e4928bbf70f1e",
          invocation_id: "tool-call-7",
          reason: "tool_parameters_not_authorized",
        },
      ],
      projection_failure: {
        reason: "raw_frame_invalid", stage: "message", location: "raw_stream_frame",
        frame_shape: {
          event_type: "content_block_delta", block_type: "other", delta_type: "text_delta",
          message_state: "open", open_block_type: "tool_use", index_state: "ignored",
          guard: "block_delta_type",
        },
      },
      executor_protocol: {
        reported: {
          task_status: "callback_failed",
          terminal_status: "completed",
          run_id_matches: true,
          fields: {
            message: {
              present: true,
              type: "string",
              bytes: 0,
              non_empty: false,
            },
            answer_receipt: { present: false, type: "missing" },
          },
          additional_field_count: 3,
        },
        validation: [
          {
            location: "$",
            type: "value_error",
            message: "Terminal result violates a protocol rule",
          },
        ],
        validation_omitted_count: 0,
        canonical: {
          status: "failed",
          error_code: "executor_protocol_invalid",
          message_non_empty: false,
          answer_receipt_present: false,
          structured_error_present: true,
        },
      },
    },
    versions: {
      run_diagnostics: "ai-platform.run-diagnostics.v1",
      runtime_diagnostics: "ai-platform.sdk-runtime-diagnostics.v1",
    },
    counts: { retained_observations: 2, omitted_observations: 0 },
  };

  adminRunsApi.monitor = async (options) => {
    calls.push("monitor");
    return monitorResponse(paginatedRuns.filter((run) => !options?.status || run.status === options.status));
  };
  adminRunsApi.investigation = async (runId) => {
    calls.push(`investigation:${runId}`);
    return investigationResponse(runId);
  };
  adminRunsApi.detail = async (runId: string) => {
    calls.push(`detail:${runId}`);
    return detail;
  };
  adminRunsApi.diagnostics = async (runId: string) => {
    calls.push(`diagnostics:${runId}`);
    return diagnostics;
  };
  adminRunsApi.trajectory = async (runId: string, afterSequence = 0) => {
    calls.push(`trajectory:${runId}:${afterSequence}`);
    if (afterSequence === 2) {
      return {
        contract_version: "ai-platform.admin-run-trajectory.v1",
        run_id: runId,
        after_sequence: 2,
        next_after_sequence: 23,
        has_more: false,
        source_count: 2,
        omitted: { private: 0, unsupported: 0, invalid: 0 },
        events: [
          { event_id: "evt_22", sequence: 22, kind: "message", source_type: "message.completed", recorded_at: null, attempt_id: "attempt-a", message_id: "msg_a", causation_event_id: null, operation_id: null, summary: "Agent 输出完成", text_length: 25 },
          { event_id: "evt_23", sequence: 23, kind: "error", source_type: "tool.failed", recorded_at: null, attempt_id: "attempt-a", message_id: null, causation_event_id: "evt_22", operation_id: "op_b", summary: "Execute", outcome: "timeout" },
        ],
      };
    }
    return {
      contract_version: "ai-platform.admin-run-trajectory.v1",
      run_id: runId,
      after_sequence: afterSequence,
      next_after_sequence: 2,
      has_more: true,
      source_count: 2,
      omitted: { private: 1, unsupported: 0, invalid: 0 },
      events: [
        { event_id: "evt_1", sequence: 1, kind: "action", source_type: "tool.started", recorded_at: null, attempt_id: "attempt-a", message_id: null, causation_event_id: null, operation_id: "op_a", summary: "Read", category: "read" },
        { event_id: "evt_2", sequence: 2, kind: "observation", source_type: "tool.completed", recorded_at: null, attempt_id: "attempt-a", message_id: null, causation_event_id: "evt_1", operation_id: "op_a", summary: "Read", outcome: "completed" },
      ],
    };
  };
  adminRunsApi.exportDiagnostics = async (runId: string) => {
    calls.push(`export:${runId}`);
    return {
      blob: new Blob(["zip"]),
      filename: "run-diagnostics-run_running.zip",
      exportId: "rdiagexp-a",
    };
  };

  const container = document.getElementById("root")!;
  const root = await createLobeTestRoot(container);
  const click = async (element: Element | undefined | null) => {
    assert.ok(element);
    await act(async () => { element.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, button: 0 })); });
  };
  const button = (text: string) => Array.from(document.querySelectorAll("button")).find((item) => item.textContent === text);
  const selectTab = async (label: string) => { await click(Array.from(document.querySelectorAll('.run-detail-tabs [role="tab"]')).find((item) => item.textContent === label)); };
  const open = async (id: string) => {
    await click(document.querySelector('a[aria-label="查看 ' + id + '"]'));
    await waitFor(() => Boolean(document.querySelector("[data-run-detail-page] h1")));
  };
  try {
    await act(async () => { root.render(<BrowserRouter><Routes><Route path="/runs" element={<RunMonitorPanel />} /><Route path="/runs/:runId" element={<RunMonitorPanel />} /></Routes></BrowserRouter>); });
    await waitFor(() => document.body.textContent?.includes("审核季度采购合同") === true);
    await click(document.querySelector('button[aria-label="暂停自动刷新"]'));
    await click(document.querySelector('button[aria-label="第 2 页"]'));
    assert.match(document.body.textContent ?? "", /run_failed_8/);
    await click(document.querySelector('button[aria-label="第 1 页"]'));
    const quick = document.querySelector('button[aria-label="快速查看 run_running"]') as HTMLButtonElement;
    quick.focus(); await click(quick);
    await waitFor(() => Boolean(document.querySelector('[data-run-quick-view]')));
    assert.equal(document.querySelector('[data-run-runtime-diagnostics]'), null, "quick view must not retain a second full investigation");
    await click(document.querySelector('button[aria-label="关闭运行详情"]'));
    await waitFor(() => !document.querySelector('[data-run-quick-view]'));
    await open("run_running");
    assert.equal(window.location.pathname, "/runs/run_running");
    assert.deepEqual(new Set(Array.from(document.querySelectorAll('.run-detail-tabs [role="tab"]'), (item) => item.textContent)), new Set(["概览", "执行过程", "结果与产物", "输入与配置", "历史与重试"]));
    await waitFor(() => diagnostics && document.body.textContent?.includes("诊断部分采集") === true);
    assert.equal(document.activeElement, document.querySelector('[data-run-detail-page] h1'));
    assert.match(document.body.textContent ?? "", /138|诊断部分采集/);
    const exportButton = () => Array.from(document.querySelectorAll('button')).find((item) => item.textContent?.includes("下载脱敏诊断包"));
    if (exportOwnership) {
      type Result = Awaited<ReturnType<typeof adminRunsApi.exportDiagnostics>>;
      const pending: Array<{runId:string;resolve:(value:Result)=>void;reject:(error:Error)=>void}> = [];
      adminRunsApi.exportDiagnostics = (runId) => new Promise((resolve,reject) => pending.push({runId,resolve,reject}));
      adminRunsApi.detail = async (id) => ({...detail,run:{...detail.run,run_id:id}});
      await click(exportButton());
      await click(Array.from(document.querySelectorAll('a')).find((a)=>a.textContent === "运行监控"));
      await waitFor(()=>Boolean(document.querySelector('[data-run-monitor]')));
      const next = exportOwnership.reopenSameRun ? "run_running" : "run_failed";
      await open(next); await click(exportButton());
      await act(async()=>{if(exportOwnership.staleFails)pending[0].reject(new Error("STALE_EXPORT_ERROR"));else pending[0].resolve({blob:new Blob(["zip"]),filename:"old.zip",exportId:null});});
      assert.doesNotMatch(document.body.textContent??"",/STALE_EXPORT_ERROR/);
      assert.equal(exportButton()?.disabled,true);
      await act(async()=>pending[1].resolve({blob:new Blob(["zip"]),filename:"current.zip",exportId:null}));
      assert.equal(exportButton()?.disabled,false);
      return;
    }
    await selectTab("执行过程");
    await waitFor(()=>calls.includes("trajectory:run_running:0"));
    await click(document.querySelector('[data-stage-id]'));
    assert.match(document.querySelector('[data-selected-stage-evidence]')?.textContent??"",/tool_parameters_not_authorized/);
    await click(button("下一步"));
    assert.match(document.querySelector('[data-run-trajectory-current]')?.textContent??"",/Read/);
    await click(button("加载后续事件"));
    await waitFor(()=>calls.includes("trajectory:run_running:2"));
    const search=document.querySelector('input[aria-label="搜索已加载事件"]') as HTMLInputElement;
    await act(async()=>{const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value")!.set!;setter.call(search,"timeout");search.dispatchEvent(new Event("input",{bubbles:true}));});
    await click(Array.from(document.querySelectorAll('[aria-label="已加载公开事件"] button')).find((item)=>item.textContent?.includes("Execute")));
    assert.match(document.querySelector('[data-run-trajectory-current]')?.textContent??"",/timeout/);
    assert.equal(document.querySelectorAll('[data-run-runtime-diagnostics]').length,1);
    assert.match(document.body.textContent??"",/ACTUAL_SDK_FAILURE_MARKER/);
    assert.match(document.body.textContent??"",/ACTUAL_STACK_TAIL_MARKER/);
    assert.match(document.body.textContent??"",/ACTUAL_CHAIN_MARKER/);
    assert.match(document.body.textContent??"",/parent_observation_id/);
    assert.equal(document.querySelectorAll('[data-sdk-tool-abort]').length,3);
    assert.equal(document.querySelectorAll('[data-run-diagnostic-loss]').length,diagnostics.losses.length);
    await selectTab("结果与产物");
    assert.match(document.querySelector('[data-run-agent-output]')?.textContent??"",/WORKER_EFFECTIVE_RESPONSE/);
    assert.match(document.body.textContent??"",/审核结果.docx/);
    assert.match(document.body.textContent??"",/文件可用性未核验/);
    await selectTab("输入与配置");
    assert.match(document.body.textContent??"",/138/);
    assert.ok(document.body.textContent?.includes(observedCost===null?"未记录":observedCost+" 最小计价单位"));
    assert.doesNotMatch(document.body.textContent??"",/PRIVATE_PROMPT_MARKER|PRIVATE_RESULT_MARKER|PRIVATE_EVENT_PAYLOAD_MARKER|PRIVATE_LEASE_PAYLOAD_MARKER|PRIVATE_AUDIT_PAYLOAD_MARKER/);
    await click(exportButton()); await waitFor(()=>calls.includes("download-click"));
    await click(Array.from(document.querySelectorAll('a')).find((a)=>a.textContent==="运行监控"));
    await waitFor(()=>Boolean(document.querySelector('[data-run-monitor]')));
  } finally {
    await act(async()=>root.unmount());
    adminRunsApi.monitor = originalMonitor;
    adminRunsApi.investigation = originalInvestigation;
    adminRunsApi.detail = originalDetail;
    adminRunsApi.diagnostics = originalDiagnostics;
    adminRunsApi.trajectory = originalTrajectory;
    adminRunsApi.exportDiagnostics = originalExportDiagnostics;
    adminRunsApi.inspection = originalInspection;

  }
});

}

test("Run Monitor correlates opaque and legacy IDs without ambiguous fallback", () => {
  const native = "sdk-native-call-1";
  const operation = "op_00a5e5319edd5ba68d82da8fcae645f7";
  const evidence = { tool_name: "Read", invocation_id: native, operation_id: operation, last_stage: "failed" };
  const indexed = indexToolDiagnosticEvidence([evidence]);
  assert.equal(indexed.get(operation), evidence);
  assert.equal(indexed.get(native), evidence);
  assert.equal(indexed.has("op_other_run"), false);
  assert.equal(indexToolDiagnosticEvidence([{ tool_name: "Read" }]).size, 0);
  assert.equal(indexToolDiagnosticEvidence([{ tool_name: "Read", invocation_id: native, operation_id: "" }]).has(native), true);
  const conflicted = indexToolDiagnosticEvidence([evidence, { tool_name: "Write", invocation_id: native, operation_id: operation }]);
  assert.equal(conflicted.size, 0);
  assert.equal(indexToolDiagnosticEvidence([evidence, { ...evidence, last_stage: "completed" }]).get(operation)?.last_stage, "completed");
});


test("Run Monitor fails closed on same-tool cross-namespace identity collisions", () => {
  const first = { tool_name: "Read", invocation_id: "sdk_A", operation_id: "op_X", reason: "failure_A" };
  const second = { tool_name: "Read", invocation_id: "op_X", operation_id: "op_Y", reason: "failure_B" };
  for (const items of [[first, second], [second, first]]) {
    const indexed = indexToolDiagnosticEvidence(items);
    assert.equal(indexed.has("op_X"), false);
    assert.equal(indexed.get("sdk_A")?.reason, "failure_A");
    assert.equal(indexed.get("op_Y")?.reason, "failure_B");
  }
});


test("SDK errors retain readable causes alongside attempt-scoped MCP codes", async () => {
  document.body.innerHTML = '<div id="root"></div>';
  const container = dom.window.document.getElementById("root") as HTMLElement;
  const root = await createLobeTestRoot(container);
  const mcp = { source: "mcp_session_call", scope: "run_attempt", exception_type: "McpError", code: 408 };
  const cases: Array<{ errors: unknown; expected: string[] }> = [
    { errors: ["legacy SDK cause", "second cause"], expected: ["legacy SDK cause", "second cause"] },
    { errors: { sdk_reported_errors: ["original SDK cause", "second cause"], mcp_session_failures: [mcp] }, expected: ["original SDK cause", "second cause", JSON.stringify(mcp)] },
    { errors: { sdk_reported_errors: "single SDK cause", mcp_session_failures: [mcp] }, expected: ["single SDK cause", JSON.stringify(mcp)] },
    { errors: { mcp_session_failures: [mcp] }, expected: [JSON.stringify(mcp)] },
    { errors: "legacy non-array", expected: [] },
    { errors: { result_errors: ["legacy object"] }, expected: [] },
    { errors: { truncated: true, size_bytes: 5000, sha256: "synthetic" }, expected: [] },
    { errors: { mcp_session_failures: "invalid shape" }, expected: [] },
    { errors: null, expected: [] },
  ];
  try {
    for (const { errors, expected } of cases) {
      const diagnostics: AdminRunDiagnosticsResponse = {
        schema_version: "ai-platform.run-diagnostics.v1", revision: 1, coverage: "full",
        run: { run_id: "run-test", session_id: null, user_id: null, workspace_id: "workspace-test", status: "failed" },
        handling: [], losses: [], attempts: [],
        details: { sdk: { errors }, tool_lifecycles: [], tool_calls: [], tool_policy_denials: [] },
        versions: {}, counts: { retained_observations: 1, omitted_observations: 0 },
      };
      await act(async () => { root.render(<RunDiagnosticsSection diagnostics={diagnostics} loading={false} error={null} />); });
      const section = Array.from(document.querySelectorAll("h4")).find((heading) => heading.textContent === "SDK 错误")?.parentElement;
      assert.deepEqual(Array.from(section?.querySelectorAll("li") ?? [], (item) => item.textContent), expected);
      assert.equal(document.querySelector("[data-sdk-http-status]"), null);
    }
  } finally {
    await act(async () => { root.unmount(); });

  }
});


test("diagnostic lookup includes root stack, handling and top-level SDK/tool facts", async () => {
  document.body.innerHTML = '<div id="root"></div>';
  const root = await createLobeTestRoot(document.getElementById('root')!);
  const diagnostics: AdminRunDiagnosticsResponse = {
    schema_version: 'ai-platform.run-diagnostics.v1', revision: 1, coverage: 'partial',
    run: {run_id:'run-a',session_id:null,user_id:null,workspace_id:'w',status:'failed'},
    root: {kind:'failure',stack:'ROOT_STACK_MARKER',message:'ROOT_MESSAGE_MARKER'},
    handling: [{kind:'handling',message:'HANDLING_MARKER'}], losses:[],attempts:[],
    details: {sdk:{errors:['SDK_MARKER']},tool_lifecycles:[],tool_calls:[{tool_name:'TOOL_MARKER'}],tool_policy_denials:[]},
    versions:{},counts:{retained_observations:0,omitted_observations:0},
  };
  try {
    await act(async()=>root.render(<RunDiagnosticsSection diagnostics={diagnostics} loading={false} error={null}/>));
    const input=document.querySelector('input[aria-label="查找已保留诊断证据"]')!;
    for(const marker of ['ROOT_STACK_MARKER','ROOT_MESSAGE_MARKER','HANDLING_MARKER','SDK_MARKER','TOOL_MARKER']) {
      await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(input,marker);input.dispatchEvent(new Event('input',{bubbles:true}));});
      assert.match(document.querySelector('[data-run-diagnostic-search-results]')?.textContent??'',new RegExp(marker));
    }
  } finally { await act(async()=>root.unmount()); }
});


test("follow latest drains pages and yields to external evidence selection and upward navigation", async () => {
  const {AdminRunTrajectoryReplay}=await import('../AdminRunTrajectoryReplay');
  document.body.innerHTML='<div id="root"></div>';
  const root=await createLobeTestRoot(document.getElementById('root')!);
  const original=adminRunsApi.trajectory;
  const reads:number[]=[];
  adminRunsApi.trajectory=async(runId,after=0)=>{
    reads.push(after);
    return {contract_version:'ai-platform.admin-run-trajectory.v1',run_id:runId,after_sequence:after,next_after_sequence:after+1,has_more:after<2,source_count:1,omitted:{private:0,unsupported:0,invalid:0},events:[{event_id:`evt-${after+1}`,sequence:after+1,kind:'action',source_type:'tool.started',summary:`Tool ${after+1}`,attempt_id:null,recorded_at:null,message_id:null,causation_event_id:null,operation_id:null}]};
  };
  try {
    await act(async()=>root.render(<AdminRunTrajectoryReplay runId="run-a" attempts={[]} messages={[]} active/>));
    await waitFor(()=>reads.length===1&&!document.querySelector('button:disabled')?.textContent?.includes('读取'));
    const follow=Array.from(document.querySelectorAll('button')).find(button=>button.textContent==='跟随最新')!;
    await act(async()=>follow.click());
    await waitFor(()=>reads.length===3);
    assert.deepEqual(reads,[0,1,2]);
    await waitFor(()=>document.querySelector('[data-run-trajectory-current]')?.textContent?.includes('Tool 3')===true);
    await act(async()=>root.render(<AdminRunTrajectoryReplay runId="run-a" attempts={[]} messages={[]} active selectedSequence={1}/>));
    await waitFor(()=>document.querySelector('[data-run-trajectory-current]')?.textContent?.includes('Tool 1')===true);
    assert.equal(follow.getAttribute('aria-pressed'),'false');
    const more=Array.from(document.querySelectorAll('button')).find(button=>button.textContent==='读取新事件')!;
    await act(async()=>more.click());
    assert.match(document.querySelector('[data-run-trajectory-current]')?.textContent??'',/Tool 1/);
    await act(async()=>follow.click());
    await act(async()=>document.querySelector('[data-run-trajectory-replay]')!.dispatchEvent(new KeyboardEvent('keydown',{key:'PageUp',bubbles:true})));
    assert.equal(follow.getAttribute('aria-pressed'),'false');
  } finally {adminRunsApi.trajectory=original;await act(async()=>root.unmount());}
});
