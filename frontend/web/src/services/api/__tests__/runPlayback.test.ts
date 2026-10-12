import test from "node:test";
import assert from "node:assert/strict";
import windows from "./fixtures/runPlaybackContextWindows.json";

import {
  buildRunPlaybackUrl,
  fetchRunPlayback,
  normalizeRunPlayback,
  type RunPlaybackResponse,
} from "../runPlayback.ts";

test("buildRunPlaybackUrl encodes run ids and appends cursor query params", () => {
  assert.equal(
    buildRunPlaybackUrl("run/with spaces", {
      after_sequence: 7,
      limit: 50,
    }),
    "/api/ai/runs/run%2Fwith%20spaces/playback?after_sequence=7&limit=50",
  );
});

test("fetchRunPlayback uses the playback URL with authFetch", async () => {
  const originalFetch = globalThis.fetch;
  const requests: Array<{ input: RequestInfo | URL; init?: RequestInit }> = [];

  globalThis.fetch = (async (input, init) => {
    requests.push({ input, init });
    return new Response(
      JSON.stringify({
        contract_version: "ai-platform.run-playback.v1",
        run_id: "run/with spaces",
      }),
      {
        status: 200,
        headers: { "Content-Type": "application/json" },
      },
    );
  }) as typeof fetch;

  try {
    const result = await fetchRunPlayback(
      "run/with spaces",
      { after_sequence: 3, limit: 10 },
      { skipAuth: true },
    );

    assert.equal(
      requests[0]?.input,
      "/api/ai/runs/run%2Fwith%20spaces/playback?after_sequence=3&limit=10",
    );
    assert.equal(requests[0]?.init?.method, "GET");
    assert.equal(result.run_id, "run/with spaces");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("normalizeRunPlayback fills missing array fields with empty arrays", () => {
  const normalized = normalizeRunPlayback({
    contract_version: "ai-platform.run-playback.v1",
    run_id: "run-1",
  } as RunPlaybackResponse);

  assert.deepEqual(normalized.timeline, []);
  assert.deepEqual(normalized.events, []);
  assert.deepEqual(normalized.artifacts, []);
  assert.deepEqual(normalized.steps, []);
  assert.equal(normalized.context_ref?.context_window.status, "degraded");
});

test("normalizeRunPlayback handles empty responses as default playback data", () => {
  const normalized = normalizeRunPlayback(null);

  assert.deepEqual(normalized.timeline, []);
  assert.deepEqual(normalized.events, []);
  assert.deepEqual(normalized.artifacts, []);
  assert.deepEqual(normalized.steps, []);
  assert.equal(normalized.context_ref?.context_window.status, "degraded");
});

test("normalizeRunPlayback keeps only the public four-part outcome contract", () => {
  const normalized = normalizeRunPlayback({
    outcome: {
      schema_version: "ai-platform.public-run-outcome.v1",
      phase: "delivery_failed",
      detail_code: "terminal_reconciliation_failed",
      what_happened: "结果同步失败。",
      retained: "已保留 2 个可查看文件。",
      next_action: "请刷新会话。",
      problem_number: "run-safe-1",
      artifact_count: 2,
      completed_step_count: 1,
      runtime_path: "/private/runtime",
    },
  } as unknown as RunPlaybackResponse);

  assert.deepEqual(normalized.outcome, {
    schema_version: "ai-platform.public-run-outcome.v1",
    phase: "delivery_failed",
    detail_code: "terminal_reconciliation_failed",
    what_happened: "结果同步失败。",
    retained: "已保留 2 个可查看文件。",
    next_action: "请刷新会话。",
    problem_number: "run-safe-1",
    artifact_count: 2,
    completed_step_count: 1,
  });
  assert.equal(JSON.stringify(normalized).includes("runtime_path"), false);
});

test("normalizeRunPlayback rejects unknown outcome phases", () => {
  const normalized = normalizeRunPlayback({
    outcome: {
      schema_version: "ai-platform.public-run-outcome.v1",
      phase: "secret_internal_phase",
      detail_code: "run_failed",
      what_happened: "失败。",
      retained: "无。",
      next_action: "联系管理员。",
      problem_number: "run-safe-2",
    },
  } as unknown as RunPlaybackResponse);

  assert.equal(normalized.outcome, undefined);
});

test("normalizeRunPlayback consumes the backend window-only contract", () => {
  for (const payload of Object.values(windows)) {
    const normalized = normalizeRunPlayback(payload);
    const expected = {
      status: payload.context_window.status,
      history_candidate_count: payload.context_window.history_candidate_count,
      history_authorized_count: payload.context_window.history_authorized_count,
      history_omitted_count: payload.context_window.history_omitted_count,
      legacy_history_excluded: payload.context_window.legacy_history_excluded,
      selected_file_names: payload.context_window.selected_file_names,
    };
    assert.deepEqual(normalized.context_ref, { context_window: expected });
    assert.equal("context_window" in normalized, false, "do not store a second owner");
  }
});

test("context window drops private and retired fields without merging the mirror", () => {
  const payload = windows.trimmed;
  const normalized = normalizeRunPlayback({
    ...payload,
    context_window: windows.complete.context_window,
    context_ref: {
      context_snapshot_id: "ctx-private",
      referenced_materials: { file_ids: ["file-private"] },
      used_context_summary: { input_keys: ["message"] },
      execution_tier: "document_worker",
      context_pack_version: "v3",
      context_window: {
        ...payload.context_ref.context_window,
        selection_version: "/private/version",
        file_ids: ["file-private"],
        skill_ids: ["skill-private"],
        payload: { secret: true },
        runtime_path: "/runtime/private",
      },
    },
  });
  assert.equal(normalized.context_ref?.context_window.status, "trimmed");
  for (const fragment of ["ctx-private", "file-private", "skill-private", "payload", "runtime_path", "document_worker", "context_pack_version", "selection_version", "/private"]) {
    assert.equal(JSON.stringify(normalized).includes(fragment), false, fragment);
  }
});

test("missing windows do not revive the top-level mirror or retired provenance", () => {
  for (const context_ref of [undefined, null, {}, { context_window: null }, { context_window: [] }, { referenced_materials: { file_count: 2 } }]) {
    const window = normalizeRunPlayback({ context_ref, context_window: windows.complete.context_window }).context_ref?.context_window;
    assert.equal(window?.status, "degraded");
    assert.deepEqual(window?.selected_file_names, []);
  }
});

test("malformed window fields fail closed to safe display values", () => {
  for (const bad of [-1, 1.5, Infinity, NaN, Number.MAX_SAFE_INTEGER + 1, "3", true, null, undefined]) {
    const normalized = normalizeRunPlayback({ context_ref: { context_window: {
      status: "complete",
      history_candidate_count: bad,
      history_authorized_count: bad,
      history_omitted_count: bad,
      legacy_history_excluded: "true",
      selected_file_names: { name: "report.pdf" },
    } } });
    assert.deepEqual(normalized.context_ref?.context_window, {
      status: "degraded", history_candidate_count: 0, history_authorized_count: 0,
      history_omitted_count: 0, legacy_history_excluded: false, selected_file_names: [],
    });
  }
  for (const status of [undefined, "private-status", null, 3]) {
    assert.equal(normalizeRunPlayback({ context_ref: { context_window: { ...windows.complete.context_window, status } } }).context_ref?.context_window.status, "degraded");
  }
});

test("selected file names preserve ordinary basenames while rejecting paths and credentials", () => {
  const normalized = normalizeRunPlayback({ context_ref: { context_window: {
    status: "complete", selected_file_names: [
      "/private/计划.docx", "C:\\private\\report.pdf", "report.pdf", " plain.txt ",
      "../", ".", "..", "bad\u0000.txt", "x".repeat(161), 3, {},
      "private%2Fsecret.txt", "private%5Csecret.txt", "file:secret.txt", "token=synthetic-secret.txt", "api_key=synthetic-secret.txt", "runtime_path.txt", "raw_skill.txt",
      ...Array.from({ length: 12 }, (_, index) => `file-${index}.pdf`),
    ],
  } } });
  assert.deepEqual(normalized.context_ref?.context_window.selected_file_names, [
    "计划.docx", "report.pdf", "plain.txt", "runtime_path.txt", "raw_skill.txt", "file-0.pdf", "file-1.pdf", "file-2.pdf",
  ]);
});

test("normalizeRunPlayback sorts timeline by sequence then created_at stably", () => {
  const normalized = normalizeRunPlayback({
    timeline: [
      {
        entry_type: "event",
        sequence: 2,
        created_at: "2026-06-03T10:00:00Z",
        event: { id: "event-2", event_type: "status", sequence: 2 },
      },
      {
        entry_type: "event",
        sequence: 1,
        created_at: "2026-06-03T10:02:00Z",
        event: { id: "event-1", event_type: "status", sequence: 1 },
      },
      {
        entry_type: "artifact",
        created_at: "2026-06-03T09:00:00Z",
        artifact: { artifact_id: "artifact-1", label: "First artifact" },
      },
      {
        entry_type: "artifact",
        created_at: "2026-06-03T09:00:00Z",
        artifact: { artifact_id: "artifact-2", label: "Second artifact" },
      },
    ],
  } as RunPlaybackResponse);

  assert.deepEqual(
    normalized.timeline.map((entry) => entry.event?.id ?? entry.artifact?.id),
    ["event-1", "event-2", "artifact-1", "artifact-2"],
  );
});

test("normalizeRunPlayback does not pass through dangerous fields", () => {
  const normalized = normalizeRunPlayback({
    payload: { raw: true },
    manifest: { raw: true },
    storage_key: "tenants/private/artifact.docx",
    runtime_path: "/tmp/runtime",
    work_dir: "/workspace/private",
    command_sha256: "secret-hash",
    sandbox_mode: "ephemeral",
    mcp_tool_ids: ["internal-tool"],
    used_skills_source: "/skills/internal",
    resource_limits: { memory_mb: 512 },
    events: [
      {
        id: "event-1",
        event_type: "status",
        sequence: 1,
        payload: { raw: true },
        storage_key: "private",
      },
    ],
    artifacts: [
      {
        artifact_id: "artifact-1",
        label: "Artifact",
        lineage: {
          source_run_id: "run-source",
          rawPayload: { secret: true },
          storageKey: "tenants/private/artifact.docx",
          runtimePath: "/tmp/private",
          workDir: "/workspace/private",
          commandSha256: "secret-hash",
          sandboxMode: "ephemeral",
          mcpToolIds: ["internal-tool"],
          usedSkillsSource: "/skills/internal",
          resourceLimits: { memory_mb: 512 },
          nested: {
            requestPayload: { secret: true },
            decisionPayload: { secret: true },
          },
        },
        manifest: { raw: true },
        storage_key: "private",
        runtime_path: "/tmp/private",
      },
    ],
    steps: [
      {
        step_id: "step-1",
        title: "Step",
        payload: { raw: true },
        work_dir: "/workspace/private",
        sandbox_mode: "ephemeral",
      },
    ],
  } as unknown as RunPlaybackResponse);

  const serialized = JSON.stringify(normalized);

  for (const dangerousField of [
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
    "rawPayload",
    "storageKey",
    "runtimePath",
    "workDir",
    "commandSha256",
    "sandboxMode",
    "mcpToolIds",
    "usedSkillsSource",
    "resourceLimits",
    "requestPayload",
    "decisionPayload",
    "source_run_id",
  ]) {
    assert.equal(
      serialized.includes(dangerousField),
      false,
      `${dangerousField} leaked into normalized playback`,
    );
  }
  assert.equal(normalized.artifacts[0]?.lineage, undefined);
});

test("normalizeRunPlayback drops unsafe lineage allowlist values", () => {
  const normalized = normalizeRunPlayback({
    artifacts: [
      {
        artifact_id: "artifact-1",
        lineage: {
          source_run_id: "run-source",
          producer_kind: "artifact",
          producer_role: "reviewer",
          source_event_id: "/tmp/runtime/event",
          source_step_id:
            "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
          source_file_id: "storageKey=private",
          checkpoint_id: ".claude/checkpoints/checkpoint-1",
          subagent_id: "raw skill qa-file-reviewer",
        },
      },
      {
        artifact_id: "artifact-2",
        lineage: {
          source_run_id: "commandSha256=secret",
          producer_kind: "artifact",
        },
      },
    ],
  } as unknown as RunPlaybackResponse);

  assert.deepEqual(normalized.artifacts[0]?.lineage, {
    producer_kind: "artifact",
    producer_role: "reviewer",
  });
  assert.deepEqual(normalized.artifacts[1]?.lineage, {
    producer_kind: "artifact",
  });
});


test("playback preserves unknown cost absence and real zero independently of tokens", () => {
  for (const amount of [null, undefined, 0, 17]) {
    const projected = normalizeRunPlayback(JSON.parse(JSON.stringify({run_id: "run-cost", events: [{
      id: "evt-cost", event_type: "run_succeeded", sequence: 1,
      cost: {estimated_cost_minor: amount},
      token_counts: {input: 3, output: 5, total: 8},
    }]})));
    assert.equal(projected.events[0].cost?.estimated_cost_minor, amount ?? undefined);
    assert.deepEqual(projected.events[0].token_counts, {input: 3, output: 5, total: 8});
  }
});
