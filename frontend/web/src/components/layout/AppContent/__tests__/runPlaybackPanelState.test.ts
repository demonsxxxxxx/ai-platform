import assert from "node:assert/strict";
import test from "node:test";
import windows from "../../../../services/api/__tests__/fixtures/runPlaybackContextWindows.json";

import { normalizeRunPlayback, type RunPlaybackResponse } from "../../../../services/api/runPlayback.ts";
import {
  buildRunPlaybackErrorViewModel,
  buildRunPlaybackLoadingViewModel,
  buildRunPlaybackPanelViewModel,
} from "../runPlaybackPanelState.ts";

const dangerousFields = [
  "payload",
  "manifest",
  "storage_key",
  "runtime_path",
  "work_dir",
  "command_sha256",
  "sandbox_mode",
  "mcp_tool_ids",
  "used_skills_source",
  "resource_limits",
];

test("buildRunPlaybackPanelViewModel exposes only public display fields", () => {
  const viewModel = buildRunPlaybackPanelViewModel({
    contract_version: "ai-platform.run-playback.v1",
    run_id: "run-1",
    payload: { raw: true },
    manifest: { internal: true },
    storage_key: "private/root",
    runtime_path: "/runtime/private",
    work_dir: "/workspace/private",
    command_sha256: "secret-sha",
    sandbox_mode: "internal",
    mcp_tool_ids: ["internal-tool"],
    used_skills_source: "/private/skills",
    resource_limits: { memory_mb: 512 },
    run: {
      run_id: "run-1",
      session_id: "session-1",
      agent_id: "agent-1",
      trace_id: "trace-1",
      status: "running",
      progress: 42,
      payload: { raw: true },
      work_dir: "/workspace/private",
    },
    timeline: [
      {
        entry_type: "event",
        sequence: 1,
        created_at: "2026-06-03T08:00:00.000Z",
        event: {
          id: "event-1",
          event_type: "run.started",
          message: "Run accepted",
          severity: "info",
          sequence: 1,
          payload: { raw: true },
          runtime_path: "/runtime/private",
        },
      },
      {
        entry_type: "artifact",
        sequence: 2,
        created_at: "2026-06-03T08:01:00.000Z",
        artifact: {
          artifact_id: "artifact-1",
          artifact_type: "report",
          label: "Summary report",
          content_type: "text/markdown",
          size_bytes: 4096,
          status: "ready",
          download_url: "/api/artifacts/artifact-1/download",
          manifest: { internal: true },
          storage_key: "private/artifact.md",
        },
      },
      {
        entry_type: "step",
        sequence: 3,
        created_at: "2026-06-03T08:02:00.000Z",
        step: {
          step_id: "step-1",
          step_kind: "coding",
          title: "Implement drawer",
          role: "coding",
          status: "succeeded",
          sequence: 3,
          sandbox_mode: "internal",
          mcp_tool_ids: ["internal-tool"],
        },
      },
    ],
    events: [],
    artifacts: [],
    steps: [],
    context_ref: {
      context_window: {
        status: "trimmed",
        history_candidate_count: 12,
        history_authorized_count: 4,
        history_omitted_count: 8,
        legacy_history_excluded: true,
        selected_file_names: ["report.pdf"],
      },
    },
  } as unknown as RunPlaybackResponse);

  assert.equal(viewModel.state, "ready");
  assert.deepEqual(viewModel.summary, {
    runId: "run-1",
    sessionId: "session-1",
    agentId: "agent-1",
    traceId: "trace-1",
    status: "running",
    progressLabel: "42%",
    errorMessage: null,
  });
  assert.deepEqual(
    viewModel.timeline.map((item) => ({
      id: item.id,
      kind: item.kind,
      label: item.label,
      status: item.status,
    })),
    [
      {
        id: "event:event-1",
        kind: "event",
        label: "Run accepted",
        status: "running",
      },
      {
        id: "artifact:artifact-1",
        kind: "artifact",
        label: "Summary report",
        status: "success",
      },
      {
        id: "step:step-1",
        kind: "step",
        label: "Implement drawer",
        status: "success",
      },
    ],
  );
  assert.deepEqual(viewModel.artifacts, [
    {
      id: "artifact-1",
      label: "Summary report",
      type: "report",
      status: "success",
      contentType: "text/markdown",
      sizeLabel: "4 KB",
      downloadUrl: "/api/artifacts/artifact-1/download",
      previewUrl: null,
      createdAt: null,
    },
  ]);
  assert.deepEqual(viewModel.contextWindow, {
    status: "trimmed",
    historyCounts: [
      { label: "candidate", value: 12 },
      { label: "authorized", value: 4 },
      { label: "omitted", value: 8 },
    ],
    legacyHistoryExcluded: true,
    selectedFileNames: ["report.pdf"],
  });
  assert.equal(viewModel.failureGuidance, null);

  const serialized = JSON.stringify(viewModel);
  for (const field of dangerousFields) {
    assert.equal(serialized.includes(field), false, `${field} leaked`);
  }
  assert.equal(serialized.includes("ctx-private"), false);
  assert.equal(serialized.includes("file-private"), false);
  assert.equal(serialized.includes("/workspace/private"), false);
});

test("timeline labels and statuses are stable for events, artifacts, and steps", () => {
  const viewModel = buildRunPlaybackPanelViewModel({
    run_id: "run-2",
    timeline: [
      {
        entry_type: "event",
        sequence: 1,
        event: {
          id: "event-1",
          event_type: "agent.failed",
          stage: "review",
          severity: "error",
        },
      },
      {
        entry_type: "artifact",
        sequence: 2,
        artifact: {
          artifact_id: "artifact-1",
          artifact_type: "patch",
          status: "created",
        },
      },
      {
        entry_type: "step",
        sequence: 3,
        step: {
          step_id: "step-1",
          step_kind: "test",
          status: "blocked",
        },
      },
    ],
    events: [],
    artifacts: [],
    steps: [],
  } as RunPlaybackResponse);

  assert.deepEqual(
    viewModel.timeline.map((item) => ({
      label: item.label,
      status: item.status,
    })),
    [
      { label: "review", status: "error" },
      { label: "patch artifact", status: "success" },
      { label: "test step", status: "blocked" },
    ],
  );
});

test("loading, error, and empty view models do not throw", () => {
  assert.deepEqual(buildRunPlaybackLoadingViewModel("run-loading"), {
    state: "loading",
    summary: {
      runId: "run-loading",
      sessionId: null,
      agentId: null,
      traceId: null,
      status: "loading",
      progressLabel: null,
      errorMessage: null,
    },
    timeline: [],
    artifacts: [],
    contextWindow: null,
    failureGuidance: null,
    errorMessage: null,
  });

  assert.deepEqual(
    buildRunPlaybackErrorViewModel("run-error", new Error("Network failed")),
    {
      state: "error",
      summary: {
        runId: "run-error",
        sessionId: null,
        agentId: null,
        traceId: null,
        status: "error",
        progressLabel: null,
        errorMessage: "Network failed",
      },
      timeline: [],
      artifacts: [],
      contextWindow: null,
      failureGuidance: {
        whatHappened: "无法刷新任务的最新状态。",
        retained: "当前连接无法确认已完成内容；后台任务可能仍在继续。",
        nextAction: "恢复网络后请先刷新或重新连接，确认状态前不要重复提交。",
        problemNumber: "run-error",
      },
      errorMessage: "Network failed",
    },
  );

  const empty = buildRunPlaybackPanelViewModel({
    run_id: "run-empty",
    timeline: [],
    events: [],
    artifacts: [],
    steps: [],
  } as RunPlaybackResponse);

  assert.equal(empty.state, "empty");
  assert.deepEqual(empty.timeline, []);
  assert.deepEqual(empty.artifacts, []);
  assert.equal(empty.contextWindow, null);
  assert.equal(empty.failureGuidance, null);
});

test("failed playback exposes the fixed four-part user guidance", () => {
  const viewModel = buildRunPlaybackPanelViewModel({
    run_id: "run-failed",
    run: {
      run_id: "run-failed",
      status: "failed",
      error_message: "任务执行已结束，但结果同步失败。",
    },
    outcome: {
      schema_version: "ai-platform.public-run-outcome.v1",
      phase: "delivery_failed",
      detail_code: "terminal_reconciliation_failed",
      what_happened: "任务执行已结束，但结果同步失败。",
      retained: "已保留 1 个可查看文件。",
      next_action: "可先下载文件，再刷新会话。",
      problem_number: "run-failed",
      artifact_count: 1,
      completed_step_count: 0,
    },
    timeline: [],
    events: [],
    artifacts: [],
    steps: [],
  });

  assert.equal(viewModel.state, "ready");
  assert.deepEqual(viewModel.failureGuidance, {
    whatHappened: "任务执行已结束，但结果同步失败。",
    retained: "已保留 1 个可查看文件。",
    nextAction: "可先下载文件，再刷新会话。",
    problemNumber: "run-failed",
  });
});

test("queued and running playback do not render failure guidance", () => {
  for (const phase of ["not_started", "in_progress"] as const) {
    const viewModel = buildRunPlaybackPanelViewModel({
      run_id: `run-${phase}`,
      outcome: {
        schema_version: "ai-platform.public-run-outcome.v1",
        phase,
        detail_code: phase === "not_started" ? "run_queued" : "run_running",
        what_happened: "任务仍在进行。",
        retained: "任务请求已保留。",
        next_action: "请继续等待。",
        problem_number: `run-${phase}`,
      },
      timeline: [],
      events: [],
      artifacts: [],
      steps: [],
    });

    assert.equal(viewModel.failureGuidance, null);
  }
});


test("backend window-only payload remains visible even when all degraded counts are zero", () => {
  for (const payload of Object.values(windows)) {
    const view = buildRunPlaybackPanelViewModel(normalizeRunPlayback(payload));
    const expected = payload.context_ref.context_window;
    assert.equal(view.state, "ready");
    assert.equal(view.contextWindow?.status, expected.status);
    assert.deepEqual(view.contextWindow?.historyCounts.map(({ value }) => value), [
      expected.history_candidate_count, expected.history_authorized_count, expected.history_omitted_count,
    ]);
    assert.deepEqual(view.contextWindow?.selectedFileNames, expected.selected_file_names);
    assert.equal(view.contextWindow?.legacyHistoryExcluded, expected.legacy_history_excluded);
  }
});
