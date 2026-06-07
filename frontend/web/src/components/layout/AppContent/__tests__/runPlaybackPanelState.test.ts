import assert from "node:assert/strict";
import test from "node:test";

import type { RunPlaybackResponse } from "../../../../services/api/runPlayback.ts";
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
    multi_agent: {
      run_id: "run-1",
      counts: {
        total: 2,
        succeeded: 1,
        running: 1,
        resource_limits: { memory_mb: 512 },
      },
      steps: [
        {
          step_id: "step-2",
          step_kind: "review",
          title: "Review implementation",
          role: "review",
          status: "running",
          sequence: 4,
          work_dir: "/workspace/private",
        },
      ],
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
  assert.deepEqual(viewModel.multiAgent, {
    counts: [
      { label: "total", value: 2 },
      { label: "succeeded", value: 1 },
      { label: "running", value: 1 },
    ],
    steps: [
      {
        id: "step-2",
        label: "Review implementation",
        role: "review",
        kind: "review",
        status: "running",
        sequence: 4,
        startedAt: null,
        finishedAt: null,
      },
    ],
  });

  const serialized = JSON.stringify(viewModel);
  for (const field of dangerousFields) {
    assert.equal(serialized.includes(field), false, `${field} leaked`);
  }
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
    multi_agent: null,
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
    multiAgent: { counts: [], steps: [] },
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
      multiAgent: { counts: [], steps: [] },
      errorMessage: "Network failed",
    },
  );

  const empty = buildRunPlaybackPanelViewModel({
    run_id: "run-empty",
    timeline: [],
    events: [],
    artifacts: [],
    steps: [],
    multi_agent: null,
  } as RunPlaybackResponse);

  assert.equal(empty.state, "empty");
  assert.deepEqual(empty.timeline, []);
  assert.deepEqual(empty.artifacts, []);
  assert.deepEqual(empty.multiAgent, { counts: [], steps: [] });
});
