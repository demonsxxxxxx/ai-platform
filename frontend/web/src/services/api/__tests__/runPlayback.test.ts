import test from "node:test";
import assert from "node:assert/strict";

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
  assert.equal(normalized.multi_agent, null);
  assert.equal(normalized.context_ref, null);
});

test("normalizeRunPlayback handles empty responses as default playback data", () => {
  const normalized = normalizeRunPlayback(null);

  assert.deepEqual(normalized.timeline, []);
  assert.deepEqual(normalized.events, []);
  assert.deepEqual(normalized.artifacts, []);
  assert.deepEqual(normalized.steps, []);
  assert.equal(normalized.multi_agent, null);
  assert.equal(normalized.context_ref, null);
});

test("normalizeRunPlayback preserves safe context provenance and drops private fields", () => {
  const normalized = normalizeRunPlayback({
    context_ref: {
      context_snapshot_id: "ctx-private",
      source: "stored_context_snapshot",
      referenced_materials: {
        file_count: 2,
        message_count: 1,
        memory_record_count: 3,
        artifact_count: 4,
        file_ids: ["file-private"],
        storage_key: "tenants/private/source.docx",
      },
      used_context_summary: {
        source: "stored_context_snapshot",
        input_keys: ["attachments", "message", "storage_key"],
        memory_policy_source: "stored",
        long_term_memory_read: false,
        file_count: 2,
        message_count: 1,
        memory_record_count: 3,
        artifact_count: 4,
        raw_path: "/workspace/private",
      },
      execution_tier: "document_worker",
      latest_artifact_version: "v7",
      context_pack_version: "v3",
      context_pack_generated_at: "2026-06-12T01:23:45Z",
      storage_key: "tenants/private/context.json",
      runtime_path: "/tmp/private",
      work_dir: "/workspace/private",
      payload: { secret: true },
    },
  } as unknown as RunPlaybackResponse);

  assert.deepEqual(normalized.context_ref, {
    source: "stored_context_snapshot",
    referenced_materials: {
      file_count: 2,
      message_count: 1,
      memory_record_count: 3,
      artifact_count: 4,
    },
    used_context_summary: {
      source: "stored_context_snapshot",
      input_keys: ["attachments", "message"],
      memory_policy_source: "stored",
      long_term_memory_read: false,
      file_count: 2,
      message_count: 1,
      memory_record_count: 3,
      artifact_count: 4,
    },
    execution_tier: "document_worker",
    latest_artifact_version: "v7",
    context_pack_version: "v3",
    context_pack_generated_at: "2026-06-12T01:23:45Z",
  });

  const serialized = JSON.stringify(normalized.context_ref);
  for (const privateFragment of [
    "ctx-private",
    "file-private",
    "storage_key",
    "runtime_path",
    "work_dir",
    "payload",
    "/workspace/private",
    "tenants/private",
  ]) {
    assert.equal(
      serialized.includes(privateFragment),
      false,
      `${privateFragment} leaked into context provenance`,
    );
  }
});

test("normalizeRunPlayback drops unsafe context provenance values", () => {
  const normalized = normalizeRunPlayback({
    context_ref: {
      source: "runtime/path",
      referenced_materials: {
        file_count: 1,
        artifact_count: 1,
      },
      used_context_summary: {
        source: "storage_key",
        input_keys: ["message"],
        memory_policy_source: "private_policy",
        long_term_memory_read: "yes",
      },
      execution_tier: "docker_admin_root",
      latest_artifact_version:
        "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
      context_pack_version: "0123456789abcdef0123456789abcdef",
      context_pack_generated_at: "/workspace/private/context.json",
    },
  } as unknown as RunPlaybackResponse);

  assert.deepEqual(normalized.context_ref, {
    referenced_materials: {
      file_count: 1,
      artifact_count: 1,
    },
    used_context_summary: {
      input_keys: ["message"],
    },
  });

  const serialized = JSON.stringify(normalized.context_ref);
  for (const privateFragment of [
    "runtime/path",
    "storage_key",
    "docker_admin_root",
    "private_policy",
    "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    "0123456789abcdef0123456789abcdef",
    "/workspace/private",
  ]) {
    assert.equal(serialized.includes(privateFragment), false);
  }
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
    multi_agent: {
      run_id: "run-1",
      steps: [
        {
          step_id: "step-2",
          title: "Nested step",
          payload: { raw: true },
          mcp_tool_ids: ["internal-tool"],
        },
      ],
      counts: {
        total: 1,
        resource_limits: { memory_mb: 512 },
      },
    },
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
