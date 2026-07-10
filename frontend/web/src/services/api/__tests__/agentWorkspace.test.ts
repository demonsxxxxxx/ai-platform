import test from "node:test";
import assert from "node:assert/strict";

import {
  buildAgentWorkspaceUrl,
  fetchAgentWorkspace,
  normalizeAgentWorkspaceProjection,
} from "../agent.ts";
import type { AgentWorkspaceProjection } from "../../../types/agent.ts";

test("buildAgentWorkspaceUrl encodes workspace agent and session query params", () => {
  assert.equal(
    buildAgentWorkspaceUrl({
      workspace_id: "default space",
      agent_id: "document/review",
      session_id: "ses a",
    }),
    "/api/agent-workspace?workspace_id=default%20space&agent_id=document%2Freview&session_id=ses%20a",
  );
});

test("fetchAgentWorkspace reads the governed projection with authFetch", async () => {
  const originalFetch = globalThis.fetch;
  const requests: Array<{ input: RequestInfo | URL; init?: RequestInit }> = [];

  globalThis.fetch = (async (input, init) => {
    requests.push({ input, init });
    return new Response(
      JSON.stringify({
        contract_version: "ai-platform.agent-workspace.v1",
        workspace_id: "default",
        selected_agent: {
          agent_id: "document-review",
          capability_id: "document_review",
          name: "Document reviewer",
          description: "Reviews documents",
          status: "active",
          version: "1.0.0",
        },
      }),
      {
        status: 200,
        headers: { "Content-Type": "application/json" },
      },
    );
  }) as typeof fetch;

  try {
    const result = await fetchAgentWorkspace(
      { workspace_id: "default", agent_id: "document-review" },
      { skipAuth: true },
    );

    assert.equal(
      requests[0]?.input,
      "/api/agent-workspace?workspace_id=default&agent_id=document-review",
    );
    assert.equal(requests[0]?.init?.method, "GET");
    assert.equal(result.workspace_id, "default");
    assert.equal(result.selected_agent?.agent_id, "document-review");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("normalizeAgentWorkspaceProjection fills missing lists with safe defaults", () => {
  const normalized = normalizeAgentWorkspaceProjection({
    workspace_id: "default",
    memory_context_policy: {
      workspace_id: "default",
    },
  } as AgentWorkspaceProjection);

  assert.equal(normalized.contract_version, "ai-platform.agent-workspace.v1");
  assert.deepEqual(normalized.agents, []);
  assert.deepEqual(normalized.sessions, []);
  assert.deepEqual(normalized.latest_runs, []);
  assert.equal(normalized.run_console.status, "idle");
  assert.deepEqual(normalized.run_console.events, []);
  assert.deepEqual(normalized.artifacts, []);
  assert.deepEqual(normalized.pending_tool_permissions, []);
  assert.equal(normalized.memory_context_policy.workspace_id, "default");
});

test("normalizeAgentWorkspaceProjection does not pass through private fields", () => {
  const normalized = normalizeAgentWorkspaceProjection({
    contract_version: "ai-platform.agent-workspace.v1",
    workspace_id: "default",
    selected_agent: {
      agent_id: "document-review",
      capability_id: "document_review",
      name: "Document reviewer",
      description: "Safe",
      status: "active",
      version: "1.0.0",
      default_skill_id: "qa-file-reviewer",
    },
    run_console: {
      run_id: "run-a",
      status: "running",
      next_after_sequence: 2,
      events: [
        {
          event_id: "evt-a",
          sequence: 1,
          event_type: "tool_permission_card",
          message: "Need approval",
          payload: {
            visible_to_user: true,
            storage_key: "tenants/private/raw.json",
            source_json: { local_path: "C:\\private\\file.docx" },
            tool_permission_card: {
              permission_request_id: "tpr-a",
              action: "execute",
            },
          },
        },
      ],
      steps: [
        {
          step_id: "step-a",
          title: "Review document",
          status: "running",
          payload: {
            summary: "Reading public artifact",
            sandbox_workdir: "/tmp/private",
          },
        },
      ],
    },
    artifacts: [
      {
        artifact_id: "art-a",
        label: "Reviewed document",
        storage_key: "tenants/private/review.docx",
        manifest: {
          source_json: { local_path: "C:\\private\\review.docx" },
        },
      },
    ],
    memory_context_policy: {
      workspace_id: "default",
      reason: "Configured for /home/private",
      latest_context: {
        referenced_materials: { artifact_count: 1 },
        payload_json: { sandbox_workdir: "/tmp/private" },
      },
    },
    source_json: { secret: true },
    sandbox_workdir: "/tmp/private",
  } as unknown as AgentWorkspaceProjection);

  const serialized = JSON.stringify(normalized);
  for (const privateFragment of [
    "storage_key",
    "local_path",
    "sandbox_workdir",
    "source_json",
    "default_skill_id",
    "qa-file-reviewer",
    "C:\\private",
    "/tmp/private",
    "tenants/private",
  ]) {
    assert.equal(
      serialized.includes(privateFragment),
      false,
      `${privateFragment} leaked into normalized Agent Workspace projection`,
    );
  }
  const permissionCard = normalized.run_console.events[0]?.payload
    ?.tool_permission_card;
  assert.equal(
    typeof permissionCard === "object" &&
      permissionCard !== null &&
      !Array.isArray(permissionCard)
      ? permissionCard.permission_request_id
      : undefined,
    "tpr-a",
  );
  assert.equal(normalized.artifacts[0]?.label, "Reviewed document");
});

test("normalizeAgentWorkspaceProjection only keeps same-origin api artifact urls", () => {
  const normalized = normalizeAgentWorkspaceProjection({
    workspace_id: "default",
    artifacts: [
      {
        artifact_id: "art-safe",
        label: "Safe artifact",
        download_url: "/api/ai/artifacts/art-safe/download",
        preview_url: "/api/ai/artifacts/art-safe/preview",
      },
      {
        artifact_id: "art-script",
        label: "Script artifact",
        download_url: "javascript:alert(1)",
        preview_url: "https://evil.example/api/ai/artifacts/art-script/preview",
      },
      {
        artifact_id: "art-private",
        label: "Private artifact",
        download_url: "C:\\private\\artifact.docx",
        preview_url: "/home/private-user/artifact.docx",
      },
    ],
  } as unknown as AgentWorkspaceProjection);

  assert.equal(
    normalized.artifacts[0]?.download_url,
    "/api/ai/artifacts/art-safe/download",
  );
  assert.equal(
    normalized.artifacts[0]?.preview_url,
    "/api/ai/artifacts/art-safe/preview",
  );
  assert.equal(normalized.artifacts[1]?.download_url, undefined);
  assert.equal(normalized.artifacts[1]?.preview_url, undefined);
  assert.equal(normalized.artifacts[2]?.download_url, undefined);
  assert.equal(normalized.artifacts[2]?.preview_url, undefined);
});
