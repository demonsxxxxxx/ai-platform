import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
// jsdom 26 ships no declarations; this test uses only its runtime constructor.
// @ts-expect-error jsdom is the pinned mounted-test runtime.
import { JSDOM } from "jsdom";
import React, { act } from "react";
import { createRoot } from "react-dom/client";

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
  buildAdminRunMonitorView,
  indexToolDiagnosticEvidence,
} from "../adminRunTimeline";
import {
  filterAdminRuns,
  RunMonitorPanel,
} from "../RunMonitorPanel";

import { RunDiagnosticsSection } from "../RunDiagnosticsSection";

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
      started_at: "2026-04-01T09:00:02Z", finished_at: "2026-04-01T09:00:03Z", offset_ms: 2000, receipt_duration_ms: 1000, duration_ms: 1200 }],
    comparison: { window: monitorResponse([]).window, scope: "same_agent_skill_profile_model_configuration", normal_run_id: "run_failed", sample_count: 2, latency: metrics, missing_reason: null } };
}

test("Run Monitor derives current activity without private events or stale terminal labels", () => {
  const events = [{ type: "agent.progress", payload: { phase: "model_wait" } }, { type: "tool.completed", visible_to_user: false, message: "PRIVATE" }, { type: "stream.heartbeat" }];
  assert.equal(buildAdminRunMonitorView(runs[0], events).currentAction, "正在等待模型响应");
  assert.equal(buildAdminRunMonitorView(runs[1], events).currentAction, "运行失败");
  assert.doesNotMatch(JSON.stringify(buildAdminRunMonitorView(runs[0], events)), /PRIVATE/);
});

test("Run Monitor filters only the explicitly projected Run identities", () => {
  assert.deepEqual(filterAdminRuns(runs, "running", "chat_2026").map((run) => run.run_id), [
    "run_running",
  ]);
  assert.deepEqual(filterAdminRuns(runs, "failed", "worker_execution_failed").map((run) => run.run_id), [
    "run_failed",
  ]);
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
  const dom = new JSDOM("<!doctype html><html><body><div id='root'></div></body></html>", {
    url: "http://localhost/runs",
    pretendToBeVisual: true,
  });
  const globalValues: Record<string, unknown> = {
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    HTMLElement: dom.window.HTMLElement,
    HTMLFormElement: dom.window.HTMLFormElement,
    HTMLSelectElement: dom.window.HTMLSelectElement,
    Node: dom.window.Node,
    Event: dom.window.Event,
    MouseEvent: dom.window.MouseEvent,
    KeyboardEvent: dom.window.KeyboardEvent,
    InputEvent: dom.window.InputEvent,
    IS_REACT_ACT_ENVIRONMENT: true,
  };
  const previousDescriptors = new Map(
    Object.keys(globalValues).map((key) => [
      key,
      Object.getOwnPropertyDescriptor(globalThis, key),
    ]),
  );
  for (const [key, value] of Object.entries(globalValues)) {
    Object.defineProperty(globalThis, key, {
      configurable: true,
      writable: true,
      value,
    });
  }
  const originalMonitor = adminRunsApi.monitor;
  const originalInvestigation = adminRunsApi.investigation;
  const originalDetail = adminRunsApi.detail;
  const originalDiagnostics = adminRunsApi.diagnostics;
  const originalTrajectory = adminRunsApi.trajectory;
  const originalExportDiagnostics = adminRunsApi.exportDiagnostics;
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
      observation_id: "obs-a",
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
        observation_id: "obs-a",
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
  adminRunsApi.investigation = async (runId, options) => {
    calls.push(`investigation:${runId}`);
    assert.equal(options?.fromAt, "2026-04-01T00:00:00Z");
    assert.equal(options?.toAt, "2026-04-02T00:00:00Z");
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

  const container = dom.window.document.getElementById("root");
  assert.ok(container);
  const root = createRoot(container);

  try {
    await act(async () => {
      root.render(React.createElement(RunMonitorPanel));
    });
    await waitFor(() => container.textContent?.includes("审核季度采购合同") === true);

    if (exportOwnership) {
      type ExportResult = Awaited<ReturnType<typeof adminRunsApi.exportDiagnostics>>;
      const pending: { runId: string; resolve: (result: ExportResult) => void; reject: (error: Error) => void }[] = [];
      adminRunsApi.exportDiagnostics = (runId) => new Promise((resolve, reject) => {
        pending.push({ runId, resolve, reject });
      });
      adminRunsApi.detail = async (runId) => ({ ...detail, run: { ...detail.run, run_id: runId } });
      const click = async (button: Element | null | undefined) => {
        assert.ok(button);
        await act(async () => { button.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true })); });
      };
      const exportButton = () => (Array.from(container.querySelectorAll("button")) as HTMLButtonElement[])
        .find((button) => /^(下载脱敏诊断包|正在生成…)$/u.test(button.textContent ?? ""));
      const open = async (runId: string) => {
        await click(container.querySelector(`button[aria-label="查看 ${runId}"]`));
        await waitFor(() => Boolean(exportButton()));
      };
      const result = (runId: string): ExportResult => ({
        blob: new Blob(["zip"]), filename: `run-diagnostics-${runId}.zip`, exportId: `export-${runId}`,
      });
      const downloads: string[] = [];
      Object.defineProperty(dom.window.HTMLAnchorElement.prototype, "click", {
        configurable: true, value: function (this: HTMLAnchorElement) { downloads.push(this.download); },
      });
      await click(container.querySelector('button[aria-label="暂停自动刷新"]'));
      await open("run_running");
      await click(exportButton());
      assert.equal(exportButton()?.disabled, true);
      await click(container.querySelector('button[aria-label="关闭运行详情"]'));
      const nextRunId = exportOwnership.reopenSameRun ? "run_running" : "run_failed";
      await open(nextRunId);
      assert.equal(exportButton()?.disabled, false);
      await click(exportButton());
      assert.deepEqual(pending.map((request) => request.runId), ["run_running", nextRunId]);
      await act(async () => {
        if (exportOwnership.staleFails) pending[0].reject(new Error("STALE_EXPORT_ERROR"));
        else pending[0].resolve(result("run_running"));
      });
      assert.doesNotMatch(container.textContent ?? "", /STALE_EXPORT_ERROR/);
      assert.equal(exportButton()?.disabled, true, "stale finally must not clear the current export busy state");
      assert.deepEqual(downloads, exportOwnership.staleFails ? [] : ["run-diagnostics-run_running.zip"]);
      // The current request still owns error and busy state, and retry still downloads.
      await act(async () => { pending[1].reject(new Error("CURRENT_EXPORT_ERROR")); });
      assert.match(container.textContent ?? "", /CURRENT_EXPORT_ERROR/);
      assert.equal(exportButton()?.disabled, false);
      await click(exportButton());
      assert.doesNotMatch(container.textContent ?? "", /CURRENT_EXPORT_ERROR/);
      await act(async () => { pending[2].resolve(result(nextRunId)); });
      assert.equal(exportButton()?.disabled, false);
      assert.equal(downloads.at(-1), `run-diagnostics-${nextRunId}.zip`);
      return;
    }

    assert.equal(calls[0], "monitor");
    const listCallCount = calls.filter((call) => call === "monitor").length;
    await act(async () => {
      dom.window.document.dispatchEvent(new dom.window.Event("visibilitychange"));
    });
    assert.equal(calls.filter((call) => call === "monitor").length, listCallCount);
    const pauseAutoRefresh = container.querySelector(
      'button[aria-label="暂停自动刷新"]',
    ) as HTMLButtonElement | null;
    assert.ok(pauseAutoRefresh);
    await act(async () => {
      pauseAutoRefresh.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
    });
    assert.ok(container.querySelector('button[aria-label="开启自动刷新"]'));
    assert.match(container.textContent ?? "", /P50/);
    assert.match(container.textContent ?? "", /P95/);
    assert.match(container.textContent ?? "", /80/);
    assert.doesNotMatch(container.textContent ?? "", /Worker 在线/);
    assert.match(container.textContent ?? "", /王敏/);
    assert.match(container.textContent ?? "", /法务工作区/);
    assert.match(container.textContent ?? "", /合同审阅助手/);
    assert.match(container.textContent ?? "", /run_failed/);
    assert.match(container.textContent ?? "", /worker_execution_failed/);
    assert.match(container.textContent ?? "", /显示 1-10 \/ 12 条/);

    const nextPageButton = container.querySelector(
      'button[aria-label="下一页"]',
    ) as HTMLButtonElement | null;
    assert.ok(nextPageButton);
    assert.equal(nextPageButton.disabled, false);
    await act(async () => {
      nextPageButton.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
    });
    await waitFor(() => container.textContent?.includes("run_failed_8") === true);
    assert.doesNotMatch(container.textContent ?? "", /run_failed_0/);
    const previousPageButton = container.querySelector(
      'button[aria-label="上一页"]',
    ) as HTMLButtonElement | null;
    assert.ok(previousPageButton);
    await act(async () => {
      previousPageButton.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
    });
    await waitFor(() => container.textContent?.includes("run_failed_0") === true);

    const openButtons = Array.from(
      container.querySelectorAll('button[aria-label="查看 run_running"]'),
    ) as HTMLButtonElement[];
    assert.ok(openButtons.length >= 1);
    openButtons[0].focus();
    await act(async () => {
      openButtons[0].dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
    });
    await waitFor(() => container.querySelector("[data-run-investigation]") !== null);
    assert.ok(calls.includes("investigation:run_running"));

    assert.ok(calls.includes("detail:run_running"));
    assert.ok(calls.includes("diagnostics:run_running"));
    await waitFor(() => calls.includes("trajectory:run_running:0"));
    assert.match(container.textContent ?? "", /事件回放/);
    const trajectoryButtons = Array.from(
      container.querySelectorAll("[data-run-trajectory-replay] button"),
    ) as HTMLButtonElement[];
    const nextTrajectoryStep = trajectoryButtons.find((button) => button.textContent === "下一步");
    assert.ok(nextTrajectoryStep);
    await act(async () => {
      nextTrajectoryStep.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
    });
    assert.match(container.querySelector("[data-run-trajectory-current]")?.textContent ?? "", /动作.*Read/);
    const loadMoreTrajectory = Array.from(container.querySelectorAll("[data-run-trajectory-replay] button"))
      .find((button) => (button as HTMLButtonElement).textContent === "加载后续事件") as HTMLButtonElement | undefined;
    assert.ok(loadMoreTrajectory);
    await act(async () => {
      loadMoreTrajectory.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
    });
    await waitFor(() => calls.includes("trajectory:run_running:2"));
    assert.match(container.querySelector("[data-run-trajectory-replay]")?.textContent ?? "", /1 \/ 4 条/);
    await act(async () => {
      nextTrajectoryStep.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
      nextTrajectoryStep.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
    });
    assert.match(container.querySelector("[data-run-trajectory-message]")?.textContent ?? "", /WORKER_EFFECTIVE_RESPONSE/);
    assert.match(container.textContent ?? "", /审核季度采购合同/);
    assert.match(container.textContent ?? "", /技术关联/);
    assert.match(container.textContent ?? "", /Run ID/);
    assert.match(container.textContent ?? "", /trace-a/);
    assert.match(container.textContent ?? "", /lease-a/);
    assert.match(container.textContent ?? "", /阶段调查/);
    assert.equal(container.querySelectorAll("[data-run-runtime-diagnostics]").length, 1);
    assert.equal(container.querySelector("[data-run-execution-journal]"), null);
    const runtimeHealth = container.querySelector("[data-run-runtime-health]");
    assert.ok(runtimeHealth);
    assert.match(runtimeHealth.textContent ?? "", /Executor 心跳正常/);
    assert.match(runtimeHealth.textContent ?? "", /异步派发已接纳/);
    assert.match(runtimeHealth.textContent ?? "", /队列 Worker 心跳（历史）/);
    assert.match(runtimeHealth.textContent ?? "", /队列租约（历史）/);
    assert.match(container.querySelector("[data-run-latency=server_first_text_ms]")?.textContent ?? "", /0 ms/);
    assert.match(container.querySelector("[data-run-latency=browser_first_text_ms]")?.textContent ?? "", /未采集/);
    assert.match(container.textContent ?? "", /138/);
    assert.ok(container.textContent?.includes(observedCost === null ? "未记录" : `${observedCost} 最小计价单位`));
    if (observedCost === null) assert.doesNotMatch(container.textContent ?? "", /0 最小计价单位/);
    assert.match(container.textContent ?? "", /下载脱敏诊断包/);
    const exportButton = (
      Array.from(container.querySelectorAll("button")) as HTMLButtonElement[]
    ).find(
      (button) => button.textContent === "下载脱敏诊断包",
    );
    assert.ok(exportButton);
    await act(async () => {
      exportButton.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
    });
    await waitFor(() => calls.includes("download-click"));
    assert.ok(calls.includes("export:run_running"));
    assert.match(container.textContent ?? "", /WORKER_EFFECTIVE_RESPONSE/);
    const stageButton = container.querySelector("[data-stage-id]");
    assert.ok(stageButton);
    await act(async () => { stageButton.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true })); });
    assert.match(container.querySelector("[data-selected-stage-evidence]")?.textContent ?? "", /tool_parameters_not_authorized/);
    assert.match(container.querySelector("[data-run-comparison]")?.textContent ?? "", /同类 P50/);
    const abortLabels = (Array.from(container.querySelectorAll("[data-sdk-tool-abort]")) as HTMLElement[]).map((item) => item.textContent);
    assert.deepEqual(abortLabels, [
      "SDK 报告工具 abort：是；不代表用户取消或 Run 取消状态。",
      "SDK 报告工具 abort：否；不代表用户取消或 Run 取消状态。",
      "SDK 报告工具 abort：未知（未采集）；不代表用户取消或 Run 取消状态。",
    ]);
    assert.match(container.textContent ?? "", /审核结果\.docx/);
    const lossEntries = Array.from(container.querySelectorAll("[data-run-diagnostic-loss]")) as HTMLElement[];
    assert.match(lossEntries[0].textContent ?? "", /脱敏.*redacted/);
    assert.match(lossEntries[0].textContent ?? "", /count 0/);
    assert.match(lossEntries[1].textContent ?? "", /长度截断/);
    assert.match(lossEntries[1].textContent ?? "", /原始字节 8192 \/ 保留字节 4096/);
    assert.match(lossEntries[2].textContent ?? "", /投影拒绝/);
    assert.match(lossEntries[3].textContent ?? "", /类别未知/);
    assert.match(lossEntries[3].textContent ?? "", /未知（未采集）/);
    assert.match(lossEntries[4].textContent ?? "", /数量截断/);
    assert.match(lossEntries[4].textContent ?? "", /原始 9 \/ 保留 8/);
    assert.doesNotMatch(container.textContent ?? "", /裁剪或拒绝/);
    assert.match(container.textContent ?? "", /执行诊断/);
    assert.match(container.textContent ?? "", /ACTUAL_SDK_FAILURE_MARKER/);
    assert.match(container.querySelector("[data-sdk-http-status]")?.textContent ?? "", /上游 HTTP 状态：429/);
    assert.match(container.textContent ?? "", /ACTUAL_STACK_TAIL_MARKER/);
    assert.match(container.textContent ?? "", /tool_parameters_not_authorized/);
    assert.match(container.textContent ?? "", /逐条观测证据/);
  const projectionEvidence = container.querySelector("[data-projection-failure-evidence]");
  assert.ok(projectionEvidence);
  assert.match(projectionEvidence.textContent ?? "", /输出校验断点/);
  assert.match(projectionEvidence.textContent ?? "", /block_delta_type/);
  assert.match(projectionEvidence.textContent ?? "", /raw_stream_frame/);
  const observationEvidence = Array.from(container.querySelectorAll("details") as NodeListOf<HTMLDetailsElement>)
    .find((item) => item.querySelector("summary")?.textContent?.includes("sdk_result_error"));
  assert.match(observationEvidence?.querySelector("pre")?.textContent ?? "", /block_delta_type/);
  assert.match(observationEvidence?.querySelector("pre")?.textContent ?? "", /"api_error_status": 429/);
    assert.match(container.textContent ?? "", /ACTUAL_CHAIN_MARKER/);
    assert.equal(container.querySelector('[aria-label="下一页事件"]'), null);
    assert.doesNotMatch(container.textContent ?? "", /DIAGNOSTIC_EVENT_BODY_MARKER/);
    assert.match(container.textContent ?? "", /artifact_manifest_invalid/);
    assert.match(container.textContent ?? "", /sdk\.exception_chain\[8\]/);
    assert.match(container.textContent ?? "", /终态协议证据/);
    assert.match(container.textContent ?? "", /上报结构（已脱敏）/);
    assert.match(container.textContent ?? "", /\$ · value_error/);
    assert.match(container.textContent ?? "", /executor_protocol_invalid/);
    assert.match(container.textContent ?? "", /历史记录|部分采集/);
    assert.doesNotMatch(container.textContent ?? "", /PRIVATE_PROMPT_MARKER/);
    assert.doesNotMatch(container.textContent ?? "", /PRIVATE_RESULT_MARKER/);
    assert.doesNotMatch(container.textContent ?? "", /PRIVATE_EVENT_PAYLOAD_MARKER/);
    assert.doesNotMatch(container.textContent ?? "", /PRIVATE_LEASE_PAYLOAD_MARKER/);
    assert.doesNotMatch(container.textContent ?? "", /PRIVATE_RELEASE_REASON_MARKER/);
    assert.doesNotMatch(container.textContent ?? "", /PRIVATE_AUDIT_PAYLOAD_MARKER/);

    const closeDetailButton = container.querySelector(
      'button[aria-label="关闭运行详情"]',
    ) as HTMLButtonElement | null;
    assert.ok(closeDetailButton);
    await waitFor(() => dom.window.document.activeElement === closeDetailButton);
    await act(async () => {
      dom.window.document.dispatchEvent(
        new dom.window.KeyboardEvent("keydown", { key: "Tab", bubbles: true }),
      );
    });
    assert.equal(dom.window.document.activeElement, closeDetailButton);
    const lastSummary = (Array.from(container.querySelectorAll('[role="dialog"] summary')) as HTMLElement[])
      .filter((item) => item.textContent === "技术关联").at(-1);
    assert.ok(lastSummary);
    await act(async () => {
      dom.window.document.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true }));
    });
    assert.equal(dom.window.document.activeElement, lastSummary, "reverse Tab must target the collapsed summary, not its hidden copy buttons");
    await act(async () => {
      dom.window.document.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Tab", bubbles: true }));
    });
    assert.equal(dom.window.document.activeElement, closeDetailButton);
    openButtons[0].focus();
    await waitFor(() => dom.window.document.activeElement === closeDetailButton);
    assert.equal(dom.window.document.activeElement, closeDetailButton);
    await act(async () => {
      dom.window.document.dispatchEvent(
        new dom.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
      );
    });
    await waitFor(() => container.querySelector('[role="dialog"]') === null);
    assert.equal(dom.window.document.activeElement, openButtons[0]);

    adminRunsApi.detail = async () => ({
      ...detail,
      runtime_health: undefined,
      worker_execution: { ...detail.worker_execution, messages: undefined },
    });
    await act(async () => {
      openButtons[0].dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
    });
    await waitFor(() => container.querySelector('[role="dialog"]') !== null);
    assert.match(container.querySelector("[data-run-runtime-health]")?.textContent ?? "", /未知/);
    assert.doesNotMatch(container.querySelector("[data-run-runtime-health]")?.textContent ?? "", /心跳正常/);
    assert.match(container.textContent ?? "", /交付结果与完整事件记录/);
    assert.match(container.textContent ?? "", /WORKER_EFFECTIVE_RESPONSE/);
    await act(async () => {
      dom.window.document.dispatchEvent(
        new dom.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
      );
    });
    await waitFor(() => container.querySelector('[role="dialog"]') === null);

    adminRunsApi.monitor = async () => monitorResponse([]);
    openButtons[0].focus();
    await act(async () => {
      openButtons[0].dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
    });
    await waitFor(() => container.querySelector('[role="dialog"]') !== null);
    const refreshButton = container.querySelector(
      'button[aria-label="刷新最近运行"]',
    ) as HTMLButtonElement | null;
    assert.ok(refreshButton);
    await act(async () => {
      refreshButton?.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
    });
    await waitFor(() => openButtons[0].isConnected === false);
    const refreshRemovalBackdrop = container.querySelector(
      "button[data-run-monitor-backdrop]",
    ) as HTMLButtonElement | null;
    assert.ok(refreshRemovalBackdrop);
    await act(async () => {
      refreshRemovalBackdrop.dispatchEvent(
        new dom.window.MouseEvent("click", { bubbles: true }),
      );
    });
    await waitFor(() => container.querySelector('[role="dialog"]') === null);
    assert.equal(dom.window.document.activeElement, refreshButton);

    adminRunsApi.monitor = async (options) => monitorResponse(runs.filter((run) => !options?.status || run.status === options.status));
    await act(async () => {
      refreshButton?.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
    });
    await waitFor(() => container.textContent?.includes("run_running") === true);

    const failedFilter = (
      Array.from(container.querySelectorAll('button[aria-pressed]')) as HTMLButtonElement[]
    ).find((button) => button.textContent === "失败");
    assert.ok(failedFilter);
    await act(async () => {
      failedFilter.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
    });
    await waitFor(() => container.textContent?.includes("run_failed") === true);
    assert.doesNotMatch(container.textContent ?? "", /chat_2026_04/);

    const partFixture = JSON.parse(readFileSync(
      new URL("../../../../../../tests/fixtures/admin-part-message.json", import.meta.url), "utf8",
    )) as { worker_execution: AdminRunDetailResponse["worker_execution"] };
    const partOpen = container.querySelector('button[aria-label="查看 run_failed"]') as HTMLButtonElement;
    for (const status of ["available", "incomplete", "invalid", "unknown"] as const) {
      adminRunsApi.detail = async () => ({
        ...detail, run: { ...detail.run, ...runs[1], status: "succeeded" },
        worker_execution: status === "available" ? partFixture.worker_execution : {
          ...partFixture.worker_execution, response: "", messages: [],
          answer_projection: { status, incomplete_messages: status === "incomplete" ? 1 : 0, invalid_messages: status === "invalid" ? 1 : 0 },
        },
      });
      await act(async () => { partOpen.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true })); });
      await waitFor(() => container.querySelector("[data-run-answer-projection]") !== null);
      assert.match(container.querySelector("[data-run-answer-projection]")?.textContent ?? "", {
        available: /已有可验证回答/, incomplete: /账本尚不完整/, invalid: /账本未通过校验/, unknown: /未采集或证据不足/,
      }[status]);
      if (status === "available") {
        assert.match(container.querySelector("[data-run-agent-output]")?.textContent ?? "", /正常公开回答/);
        assert.match(container.querySelector("[data-worker-execution-content]")?.textContent ?? "", /正常公开回答/);
      } else {
        assert.equal(container.querySelectorAll("[data-run-agent-output]").length, 0);
      }
      assert.doesNotMatch(container.querySelector("[data-worker-execution-content]")?.textContent ?? "", /WORK_TEXT_MUST_NOT_BE_ANSWER|PRIVATE_RESULT_MARKER/);
      await act(async () => {
        (container.querySelector("button[data-run-monitor-backdrop]") as HTMLButtonElement).dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
      });
      await waitFor(() => container.querySelector('[role="dialog"]') === null);
    }

    let resolvePendingDetail:
      | ((value: AdminRunDetailResponse) => void)
      | undefined;
    let resolvePendingDiagnostics:
      | ((value: AdminRunDiagnosticsResponse) => void)
      | undefined;
    adminRunsApi.detail = async () =>
      new Promise<AdminRunDetailResponse>((resolve) => {
        resolvePendingDetail = resolve;
      });
    adminRunsApi.diagnostics = async () =>
      new Promise<AdminRunDiagnosticsResponse>((resolve) => {
        resolvePendingDiagnostics = resolve;
      });
    const failedOpenButton = container.querySelector(
      'button[aria-label="查看 run_failed"]',
    ) as HTMLButtonElement | null;
    assert.ok(failedOpenButton);
    failedOpenButton.focus();
    await act(async () => {
      failedOpenButton.dispatchEvent(
        new dom.window.MouseEvent("click", { bubbles: true }),
      );
    });
    await waitFor(
      () => resolvePendingDetail !== undefined && resolvePendingDiagnostics !== undefined,
    );
    const pendingBackdrop = container.querySelector(
      "button[data-run-monitor-backdrop]",
    ) as HTMLButtonElement | null;
    assert.ok(pendingBackdrop);
    await act(async () => {
      pendingBackdrop.dispatchEvent(
        new dom.window.MouseEvent("click", { bubbles: true }),
      );
    });
    await waitFor(() => container.querySelector('[role="dialog"]') === null);
    assert.equal(dom.window.document.activeElement, failedOpenButton);
    await act(async () => {
      resolvePendingDetail?.({
        ...detail,
        run: { ...runs[1], trace_id: "STALE_DETAIL_MARKER" },
      });
      resolvePendingDiagnostics?.({
        ...diagnostics,
        root: { ...diagnostics.root!, message: "STALE_DIAGNOSTICS_MARKER" },
      });
      await Promise.resolve();
    });
    assert.equal(container.querySelector('[role="dialog"]'), null);
    assert.doesNotMatch(container.textContent ?? "", /STALE_DETAIL_MARKER/);
    assert.doesNotMatch(container.textContent ?? "", /STALE_DIAGNOSTICS_MARKER/);

    const allFilter = (
      Array.from(container.querySelectorAll('button[aria-pressed]')) as HTMLButtonElement[]
    ).find((button) => button.textContent === "全部");
    assert.ok(allFilter);
    await act(async () => {
      allFilter.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
    });

    adminRunsApi.monitor = async () => monitorResponse([runs[1]]);
    const refreshButtonForManualCheck = container.querySelector(
      'button[aria-label="刷新最近运行"]',
    ) as HTMLButtonElement | null;
    assert.ok(refreshButtonForManualCheck);
    await act(async () => {
      refreshButtonForManualCheck.dispatchEvent(
        new dom.window.MouseEvent("click", { bubbles: true }),
      );
    });
    await waitFor(() => container.textContent?.includes("run_failed") === true);
    assert.doesNotMatch(container.textContent ?? "", /run_running/);
  } finally {
    await act(async () => {
      root.unmount();
    });
    adminRunsApi.monitor = originalMonitor;
    adminRunsApi.investigation = originalInvestigation;
    adminRunsApi.detail = originalDetail;
    adminRunsApi.diagnostics = originalDiagnostics;
    adminRunsApi.trajectory = originalTrajectory;
    adminRunsApi.exportDiagnostics = originalExportDiagnostics;
    dom.window.close();
    for (const [key, descriptor] of previousDescriptors) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete (globalThis as Record<string, unknown>)[key];
    }
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
  const dom = new JSDOM("<!doctype html><div id='root'></div>");
  const globals: Record<string, unknown> = {
    window: dom.window, document: dom.window.document, IS_REACT_ACT_ENVIRONMENT: true,
  };
  const previous = new Map(Object.keys(globals).map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  for (const [key, value] of Object.entries(globals)) {
    Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
  }
  const container = dom.window.document.getElementById("root") as HTMLElement;
  const root = createRoot(container);
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
      const section = Array.from(container.querySelectorAll("h4")).find((heading) => heading.textContent === "SDK 错误")?.parentElement;
      assert.deepEqual(Array.from(section?.querySelectorAll("li") ?? [], (item) => item.textContent), expected);
      assert.equal(container.querySelector("[data-sdk-http-status]"), null);
    }
  } finally {
    await act(async () => { root.unmount(); });
    dom.window.close();
    for (const [key, descriptor] of previous) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete (globalThis as Record<string, unknown>)[key];
    }
  }
});
