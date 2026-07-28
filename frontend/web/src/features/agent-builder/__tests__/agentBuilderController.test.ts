import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

import type { ModelOption } from "../../../services/api/modelPublic";
import type { PublicSkillResponse, ToolState } from "../../../types";
import {
  mapAuthorizedBuilderSkills,
  mapSafeBuilderMcpTools,
  prepareAgentBuilderSubmission,
  type AgentBuilderCurrentCatalog,
  type AgentBuilderDraft,
} from "../agentBuilderAdapter";
import { AgentBuilderController } from "../agentBuilderController";

function skill(overrides: Partial<PublicSkillResponse> = {}): PublicSkillResponse {
  return {
    name: "document-review",
    expected_version: "2026.07.27",
    input_modes: [],
    requires_file: false,
    description: "Review an authorized document.",
    tags: [],
    enabled: true,
    source: "manual",
    files: {},
    file_count: 0,
    installed_from: "manual",
    is_published: false,
    marketplace_is_active: false,
    ...overrides,
  };
}

const model: ModelOption = {
  id: "model-id",
  value: "platform/model",
  label: "Platform model",
};

function draft(overrides: Partial<AgentBuilderDraft> = {}): AgentBuilderDraft {
  return {
    message: "Review the current document",
    instructions: "LOCAL ONLY: do not submit this text",
    model,
    selectedSkill: skill(),
    selectedMcpToolIds: ["mcp:knowledge:search", "mcp:knowledge:search"],
    ...overrides,
  };
}

function catalog(overrides: Partial<AgentBuilderCurrentCatalog> = {}): AgentBuilderCurrentCatalog {
  return {
    skills: [skill()],
    mcpTools: [
      {
        id: "mcp:knowledge:search",
        label: "Knowledge search",
        description: "Search the authorized knowledge base.",
      },
    ],
    models: [model],
    skillsResolved: true,
    mcpToolsResolved: true,
    modelsResolved: true,
    effectivePermissionsKnown: true,
    ...overrides,
  };
}

test("prepares one authorized submission without persisting local instructions", () => {
  const prepared = prepareAgentBuilderSubmission(draft(), catalog());
  assert.equal(prepared.kind, "ready");
  if (prepared.kind !== "ready") return;

  assert.deepEqual(prepared.submission.selectedSkill, {
    skill_id: "document-review",
    expected_version: "2026.07.27",
  });
  assert.deepEqual(prepared.submission.agentOptions, { model_id: "model-id" });
  assert.deepEqual(prepared.submission.selectedMcpToolIds, [
    "mcp:knowledge:search",
  ]);
  assert.equal("instructions" in prepared.submission, false);
});

test("submits through the existing Chat seam then holds only observed session and run IDs", async () => {
  const controller = new AgentBuilderController();
  const calls: unknown[][] = [];
  const chat = {
    sendMessage: async (...args: unknown[]) => {
      calls.push(args);
      return { status: "accepted" as const };
    },
  };

  const pending = await controller.submit(draft(), catalog(), chat as never);
  assert.deepEqual(pending, { phase: "awaiting_chat_identity", generation: 0 });
  assert.deepEqual(calls, [
    [
      "Review the current document",
      { model_id: "model-id" },
      undefined,
      { skill_id: "document-review", expected_version: "2026.07.27" },
      ["mcp:knowledge:search"],
    ],
  ]);

  const handoff = controller.acceptChatIdentity({
    sessionId: "session-42",
    runId: "run-42",
  });
  assert.deepEqual(handoff, {
    phase: "handoff_ready",
    generation: 0,
    identity: { sessionId: "session-42", runId: "run-42" },
    path: "/chat/session-42",
  });
});

test("forwards a published Agent selection as the sole execution selector", async () => {
  const controller = new AgentBuilderController();
  const calls: unknown[][] = [];
  const profileDraft = draft({
    selectedAgentProfile: {
      agent_id: "profile-doc-review",
      expected_revision: 7,
    },
  });

  await controller.submit(profileDraft, catalog({ modelsResolved: false }), {
    sendMessage: async (...args: unknown[]) => {
      calls.push(args);
      return { status: "accepted" as const };
    },
  } as never);

  assert.deepEqual(calls, [
    [
      "Review the current document",
      {},
      undefined,
      null,
      [],
      { agent_id: "profile-doc-review", expected_revision: 7 },
    ],
  ]);
});

test("surfaces a Chat submission failure without inventing a session or assistant output", async () => {
  const controller = new AgentBuilderController();
  const state = await controller.submit(draft(), catalog(), {
    sendMessage: async () => ({ status: "failed" }),
  } as never);

  assert.deepEqual(state, {
    phase: "error",
    generation: 0,
    code: "chat_submit_failed",
  });
  assert.deepEqual(controller.acceptChatIdentity({ sessionId: "x", runId: "y" }), state);
});

test("ignores a late submission result after the draft generation changes", async () => {
  const controller = new AgentBuilderController();
  let resolveSubmission: ((value: { status: "accepted" }) => void) | undefined;
  const pending = controller.submit(draft(), catalog(), {
    sendMessage: () =>
      new Promise((resolve) => {
        resolveSubmission = resolve;
      }),
  } as never);

  const nextGeneration = controller.invalidateDraft();
  resolveSubmission?.({ status: "accepted" });
  const settled = await pending;

  assert.deepEqual(nextGeneration, { phase: "ready", generation: 1 });
  assert.deepEqual(settled, { phase: "ready", generation: 1 });
  assert.deepEqual(
    controller.acceptChatIdentity({ sessionId: "stale-session", runId: "stale-run" }),
    { phase: "ready", generation: 1 },
  );
});

test("allows only one existing Chat submission while admission is pending", async () => {
  const controller = new AgentBuilderController();
  const calls: unknown[][] = [];
  let resolveSubmission: ((value: { status: "accepted" }) => void) | undefined;
  const chat = {
    sendMessage: (...args: unknown[]) => {
      calls.push(args);
      return new Promise<{ status: "accepted" }>((resolve) => {
        resolveSubmission = resolve;
      });
    },
  };

  const first = controller.submit(draft(), catalog(), chat as never);
  const duplicate = await controller.submit(draft(), catalog(), chat as never);
  assert.deepEqual(duplicate, { phase: "submitting", generation: 0 });
  assert.equal(calls.length, 1);

  resolveSubmission?.({ status: "accepted" });
  assert.deepEqual(await first, {
    phase: "awaiting_chat_identity",
    generation: 0,
  });
  assert.equal(calls.length, 1);
});

test("keeps requires_file Skills disabled until a real attachment handle exists", () => {
  const prepared = prepareAgentBuilderSubmission(
    draft({ selectedSkill: skill({ requires_file: true }) }),
    catalog({ skills: [skill({ requires_file: true })] }),
  );
  assert.equal(prepared.kind, "blocked");
  if (prepared.kind !== "blocked") return;
  assert.equal(prepared.code, "file_attachment_unavailable");
});

test("maps public Skills fail-closed and MCP catalog entries to safe identity only", () => {
  assert.deepEqual(
    mapAuthorizedBuilderSkills({
      skills: [skill()],
      catalogReadResolved: true,
      effectivePermissionsKnown: false,
    }),
    [],
  );

  const tools: Array<ToolState & { label?: string }> = [
    {
      name: "mcp:knowledge:search",
      label: "Knowledge search",
      description: "Search the authorized knowledge base.",
      category: "mcp",
      server: "private-server-name",
      parameters: [{ name: "secret", type: "string", description: "private", required: true }],
      enabled: true,
    },
  ];
  assert.deepEqual(mapSafeBuilderMcpTools(tools), [
    {
      id: "mcp:knowledge:search",
      label: "Knowledge search",
      description: "Search the authorized knowledge base.",
    },
  ]);
});

test("controller delegates admission to useAgent and does not manufacture Chat state", () => {
  const source = readFileSync(
    join(process.cwd(), "src/features/agent-builder/agentBuilderController.ts"),
    "utf8",
  );
  const imports = source
    .split(/\r?\n/)
    .filter((line) => line.startsWith("import "))
    .join("\n");

  assert.match(source, /ReturnType<UseAgentReturn\["sendMessage"\]>/);
  assert.match(source, /preparation\.submission\.selectedMcpToolIds/);
  assert.match(source, /APP_ROUTE_PATHS\.chat/);
  assert.doesNotMatch(imports, /sessionApi/);
  assert.doesNotMatch(source, /createSession/);
  assert.doesNotMatch(source, /optimisticMessages/);
});
