import assert from "node:assert/strict";
import test from "node:test";
// jsdom 26 ships no declarations; this test uses only its runtime constructor.
// @ts-expect-error jsdom is the pinned mounted-test runtime.
import { JSDOM } from "jsdom";
import React, { act } from "react";
import { createRoot } from "react-dom/client";

import { adminRunsApi, type AdminRunDetailResponse, type AdminRunSummary } from "../../../services/api/adminRuns";
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
        runtime_diagnostics: {
          error_code: "claude_agent_sdk_tool_admission_failed",
          failure_source: "sdk_result_error",
          sdk: { errors: ["ACTUAL_SDK_FAILURE_MARKER"] },
          tool_policy_denials: [
            {
              tool_name: "Bash",
              invocation_id: "tool-call-7",
              reason: "tool_parameters_not_authorized",
              tool_input: { command: "printf ACTUAL_TOOL_INPUT_MARKER" },
            },
          ],
        },
      },
    },
    events: [
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

  adminRunsApi.list = async () => {
    calls.push("list");
    return { runs, limit: 50 };
  };
  adminRunsApi.detail = async (runId: string) => {
    calls.push(`detail:${runId}`);
    return detail;
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
    assert.match(container.textContent ?? "", /Worker 在线/);
    assert.match(container.textContent ?? "", /run_failed/);
    assert.match(container.textContent ?? "", /worker_execution_failed/);

    const openButtons = Array.from(
      container.querySelectorAll('button[aria-label="查看 run_running"]'),
    ) as HTMLButtonElement[];
    assert.ok(openButtons.length >= 1);
    openButtons[0].focus();
    await act(async () => {
      openButtons[0].dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
    });
    await waitFor(() => container.textContent?.includes("Worker 已领取请求") === true);

    assert.ok(calls.includes("detail:run_running"));
    assert.match(container.textContent ?? "", /trace-a/);
    assert.match(container.textContent ?? "", /worker_setup/);
    assert.match(container.textContent ?? "", /lease-a/);
    assert.match(container.textContent ?? "", /执行诊断/);
    assert.match(container.textContent ?? "", /ACTUAL_SDK_FAILURE_MARKER/);
    assert.match(container.textContent ?? "", /ACTUAL_TOOL_INPUT_MARKER/);
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
      dom.window.document.dispatchEvent(new dom.window.Event("visibilitychange"));
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
      dom.window.document.dispatchEvent(new dom.window.Event("visibilitychange"));
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
    adminRunsApi.detail = async () =>
      new Promise<AdminRunDetailResponse>((resolve) => {
        resolvePendingDetail = resolve;
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
    await waitFor(() => resolvePendingDetail !== undefined);
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
      await Promise.resolve();
    });
    assert.equal(container.querySelector('[role="dialog"]'), null);
    assert.doesNotMatch(container.textContent ?? "", /STALE_DETAIL_MARKER/);

    const allFilter = (
      Array.from(container.querySelectorAll('button[aria-pressed]')) as HTMLButtonElement[]
    ).find((button) => button.textContent === "全部");
    assert.ok(allFilter);
    await act(async () => {
      allFilter.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
    });

    let listCalls = 0;
    let resolveOlderList:
      | ((value: { runs: AdminRunSummary[]; limit: number }) => void)
      | undefined;
    adminRunsApi.list = async () => {
      listCalls += 1;
      if (listCalls === 1) {
        return new Promise((resolve) => {
          resolveOlderList = resolve;
        });
      }
      return { runs: [runs[1]], limit: 50 };
    };
    const refreshButtonForRace = container.querySelector(
      'button[aria-label="刷新最近运行"]',
    ) as HTMLButtonElement | null;
    assert.ok(refreshButtonForRace);
    await act(async () => {
      refreshButtonForRace.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
    });
    await waitFor(() => resolveOlderList !== undefined);
    await act(async () => {
      dom.window.document.dispatchEvent(new dom.window.Event("visibilitychange"));
    });
    await waitFor(() => listCalls === 2 && container.textContent?.includes("run_failed") === true);
    await act(async () => {
      resolveOlderList?.({ runs: [runs[0]], limit: 50 });
      await Promise.resolve();
    });
    assert.match(container.textContent ?? "", /run_failed/);
    assert.doesNotMatch(container.textContent ?? "", /run_running/);
  } finally {
    await act(async () => {
      root.unmount();
    });
    adminRunsApi.list = originalList;
    adminRunsApi.detail = originalDetail;
    dom.window.close();
    for (const [key, descriptor] of previousDescriptors) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete (globalThis as Record<string, unknown>)[key];
    }
  }
});
