import assert from "node:assert/strict";
import test from "node:test";
// jsdom 26 ships no declarations; this test uses only its runtime constructor.
// @ts-expect-error jsdom is the pinned mounted-test runtime.
import { JSDOM } from "jsdom";
import React, { act } from "react";
import { createRoot } from "react-dom/client";

import {
  adminRunsApi,
  type AdminRunDiagnosticsResponse,
  type AdminRunDetailResponse,
  type AdminRunSummary,
} from "../../../services/api/adminRuns";
import {
  buildAdminRunEventDiagnostics,
  buildAdminRunMonitorView,
} from "../adminRunTimeline";
import { filterAdminRuns, RunMonitorPanel, summarizeAdminRuns } from "../RunMonitorPanel";

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
    user_id: "user-a",
    workspace_id: "workspace-a",
    trace_id: "trace-a",
    status: "running",
    agent_id: "agent-review",
    skill_id: "skill-docx",
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
    user_id: "user-b",
    workspace_id: "workspace-b",
    status: "failed",
    agent_id: "agent-code",
    skill_id: "skill-python",
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

test("Run Monitor compacts queue aliases and explains executor failures", () => {
  const view = buildAdminRunMonitorView(
    runs[1],
    [
      { event_id: "queue-1", type: "run_queued", message: "任务已进入队列" },
      { event_id: "skill-1", type: "skill_selected", message: "已选择后台能力" },
      {
        event_id: "queue-2",
        type: "queued",
        message: "任务队列接纳完成",
        payload: { queue_position: 2 },
      },
      { event_id: "renew-1", type: "sandbox_lease_renewed" },
      {
        event_id: "failed-1",
        type: "run_failed",
        message: "Run failed",
        error_code: "executor_failure",
      },
    ],
    {
      root: null,
      details: {
        sdk: { exception_message: "Model provider unavailable" },
      },
    } as AdminRunDiagnosticsResponse,
  );

  const queueItems = view.recentActivity.filter((item) => item.label === "已进入队列");
  assert.equal(queueItems.length, 1);
  assert.equal(queueItems[0]?.status, "info");
  assert.equal(view.recentActivity.some((item) => item.label === "活动更新"), false);
  assert.equal(view.recentActivity.some((item) => item.detail?.includes("Model provider unavailable")), true);
  assert.equal(view.recentActivity.some((item) => item.detail?.includes("sandbox_lease_renewed")), false);
});

test("Run Monitor keeps every message diagnostic for pagination", () => {
  const diagnostics = buildAdminRunEventDiagnostics(
    Array.from({ length: 81 }, (_, index) => ({
      event_id: `delta-${index}`,
      sequence: index + 1,
      type: "message.delta",
      payload: { delta: "x" },
    })),
  );

  assert.equal(diagnostics.length, 81);
  assert.equal(diagnostics.at(0)?.id, "delta-0");
  assert.equal(diagnostics.at(-1)?.id, "delta-80");
});

test("Run Monitor filters only the explicitly projected Run identities", () => {
  assert.deepEqual(filterAdminRuns(runs, "running", "chat_2026").map((run) => run.run_id), [
    "run_running",
  ]);
  assert.deepEqual(filterAdminRuns(runs, "failed", "worker_execution_failed").map((run) => run.run_id), [
    "run_failed",
  ]);
  assert.deepEqual(summarizeAdminRuns(runs), { queued: 0, running: 1, failed: 1 });
});

test("Run Monitor mounts recent Worker state and renders only authorized diagnostics", async () => {
  const dom = new JSDOM("<!doctype html><html><body><div id='root'></div></body></html>", {
    url: "http://localhost/runs",
    pretendToBeVisual: true,
  });
  const globalValues: Record<string, unknown> = {
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    HTMLElement: dom.window.HTMLElement,
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
  const originalList = adminRunsApi.list;
  const originalDetail = adminRunsApi.detail;
  const originalDiagnostics = adminRunsApi.diagnostics;
  const calls: string[] = [];

  Object.defineProperty(dom.window.HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: () => undefined,
  });

  const detail = {
    run: {
      ...runs[0],
      input: { prompt: "PRIVATE_PROMPT_MARKER" },
      result: {
        text: "PRIVATE_RESULT_MARKER",
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
        started_at: "2026-04-01T09:00:02Z",
        finished_at: "2026-04-01T09:00:03Z",
      },
    ],
    sandbox_leases: [
      {
        lease_id: "lease-a",
        status: "active",
        provider: "opensandbox",
        sandbox_mode: "ephemeral",
        release_reason: "PRIVATE_RELEASE_REASON_MARKER /runtime/secret",
        lease_payload: { runtime_path: "PRIVATE_LEASE_PAYLOAD_MARKER" },
      },
    ],
    audit: [{ payload: { credential: "PRIVATE_AUDIT_PAYLOAD_MARKER" } }],
  } as unknown as AdminRunDetailResponse;
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
    losses: Array.from({ length: 9 }, (_, index) => ({
      field: `sdk.exception_chain[${index}]`,
      reason: "truncated",
      original: 9,
      retained: 8,
    })),
    attempts: [
      {
        attempt_id: "attempt-a",
        ordinal: 1,
        status: "failed",
        owner_kind: "queue_worker",
        terminal_reason: "run_failed",
        error_code: "claude_agent_sdk_tool_admission_failed",
      },
    ],
    details: {
      schema_version: "ai-platform.sdk-runtime-diagnostics.v1",
      sdk: { errors: ["ACTUAL_SDK_FAILURE_MARKER"] },
      observations: [
        {
          observation_id: "obs-a",
          attempt_id: "attempt-a",
          source: "sdk_result_error",
          stage: "model_wait",
          error_code: "claude_agent_sdk_tool_admission_failed",
          sdk: {
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
          tool_calls: [],
          tool_policy_denials: [
            {
              tool_name: "Bash",
              invocation_id: "tool-call-7",
              reason: "tool_parameters_not_authorized",
            },
          ],
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
          tool_name: "Bash",
          invocation_id: "tool-call-7",
          reason: "tool_parameters_not_authorized",
        },
      ],
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

  adminRunsApi.list = async () => {
    calls.push("list");
    return { runs: paginatedRuns, limit: 50 };
  };
  adminRunsApi.detail = async (runId: string) => {
    calls.push(`detail:${runId}`);
    return detail;
  };
  adminRunsApi.diagnostics = async (runId: string) => {
    calls.push(`diagnostics:${runId}`);
    return diagnostics;
  };

  const container = dom.window.document.getElementById("root");
  assert.ok(container);
  const root = createRoot(container);

  try {
    await act(async () => {
      root.render(React.createElement(RunMonitorPanel));
    });
    await waitFor(() => container.textContent?.includes("chat_2026_04") === true);

    assert.equal(calls[0], "list");
    const listCallCount = calls.filter((call) => call === "list").length;
    await act(async () => {
      dom.window.document.dispatchEvent(new dom.window.Event("visibilitychange"));
    });
    assert.equal(calls.filter((call) => call === "list").length, listCallCount);
    assert.equal(container.querySelector('button[aria-label="暂停自动刷新"]'), null);
    assert.equal(container.querySelector('button[aria-label="开启自动刷新"]'), null);
    assert.match(container.textContent ?? "", /Worker 在线/);
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
    await waitFor(() => container.textContent?.includes("开始执行") === true);

    assert.ok(calls.includes("detail:run_running"));
    assert.ok(calls.includes("diagnostics:run_running"));
    assert.match(container.textContent ?? "", /trace-a/);
    assert.match(container.textContent ?? "", /worker_setup/);
    assert.match(container.textContent ?? "", /lease-a/);
    assert.match(container.textContent ?? "", /执行诊断/);
    assert.match(container.textContent ?? "", /ACTUAL_SDK_FAILURE_MARKER/);
    assert.match(container.textContent ?? "", /ACTUAL_STACK_TAIL_MARKER/);
    assert.match(container.textContent ?? "", /tool_parameters_not_authorized/);
    assert.match(container.textContent ?? "", /逐条观测证据/);
    assert.match(container.textContent ?? "", /ACTUAL_CHAIN_MARKER/);
    assert.match(container.textContent ?? "", /message.delta/);
    assert.match(container.textContent ?? "", /第 1 \/ 2 页 · 共 21 条/);
    assert.doesNotMatch(container.textContent ?? "", /DIAGNOSTIC_EVENT_BODY_MARKER/);
    const nextDiagnosticPageButton = container.querySelector(
      '[role="dialog"] button[aria-label="下一页事件"]',
    ) as HTMLButtonElement | null;
    assert.ok(nextDiagnosticPageButton);
    await act(async () => {
      nextDiagnosticPageButton.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
    });
    assert.match(container.textContent ?? "", /第 2 \/ 2 页 · 共 21 条/);
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

    adminRunsApi.list = async () => ({ runs: [], limit: 50 });
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

    adminRunsApi.list = async () => ({ runs, limit: 50 });
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
    assert.match(container.textContent ?? "", /run_failed/);
    assert.doesNotMatch(container.textContent ?? "", /chat_2026_04/);

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

    adminRunsApi.list = async () => ({ runs: [runs[1]], limit: 50 });
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
    adminRunsApi.list = originalList;
    adminRunsApi.detail = originalDetail;
    adminRunsApi.diagnostics = originalDiagnostics;
    dom.window.close();
    for (const [key, descriptor] of previousDescriptors) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete (globalThis as Record<string, unknown>)[key];
    }
  }
});
