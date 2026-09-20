import assert from "node:assert/strict";
import test from "node:test";

import type { MessagePart } from "../../../../types";
import {
  getVisibleMessageParts,
  isWorkActivityPart,
} from "../messagePartVisibility.ts";

test("classifies every work activity without hiding answers, artifacts, or actionable status", () => {
  for (const type of [
    "tool",
    "subagent",
    "execution_step",
    "execution_process",
    "todo",
    "summary",
  ] as const) {
    assert.equal(isWorkActivityPart({ type } as MessagePart), true, type);
  }
  for (const type of [
    "text",
    "thinking",
    "sandbox",
    "artifact",
    "run_status",
    "tool_permission",
  ] as const) {
    assert.equal(isWorkActivityPart({ type } as MessagePart), false, type);
  }
});

test("keeps only schema-shaped public tool lifecycle visible", () => {
  const rawTool: MessagePart = {
    type: "tool",
    name: "Bash",
    args: { command: "cat /workspace/private --token secret" },
    result: "private command output",
  };
  const publicTool: MessagePart = {
    type: "tool",
    id: "operation-read-1",
    name: "Bash: cat /workspace/private",
    args: {},
    status: "completed",
    public_operation_id: "operation-read-1",
    public_category: "read",
  };

  const unknownTool: MessagePart = {
    ...publicTool,
    id: "operation-unknown-1",
    public_operation_id: "operation-unknown-1",
    public_category: "future-private-category",
  };
  const malformedId: MessagePart = {
    ...publicTool,
    public_operation_id: "../../private-operation",
  };
  const missingStatus: MessagePart = {
    ...publicTool,
    status: undefined,
  };
  const unknownStatus = {
    ...publicTool,
    status: "waiting-for-private-result",
  } as unknown as MessagePart;

  const visible = getVisibleMessageParts([
    rawTool,
    unknownTool,
    malformedId,
    missingStatus,
    unknownStatus,
    publicTool,
  ]);
  assert.equal(visible.length, 1);
  assert.deepEqual(visible[0], {
    type: "tool",
    name: "Read",
    args: {},
    status: "completed",
    isPending: false,
    depth: undefined,
    public_operation_id: "operation-read-1",
    public_category: "read",
    duration_ms: undefined,
  });
  assert.doesNotMatch(JSON.stringify(visible), /command|result|private/);
});

test("shows only the bounded v4 Skill display name", () => {
  const visible = getVisibleMessageParts([
    {
      type: "tool",
      name: "raw-skill-name /workspace/private",
      args: { command: "cat private-token" },
      status: "completed",
      public_operation_id: "operation-skill-1",
      public_display_name: "QA Review",
      public_category: "skill",
    },
    {
      type: "tool",
      name: "raw-read-name",
      args: {},
      status: "completed",
      public_operation_id: "operation-read-2",
      public_display_name: "ignored /workspace/private",
      public_category: "read",
    },
    {
      type: "tool",
      name: "raw-invalid-skill-name",
      args: {},
      status: "completed",
      public_operation_id: "operation-skill-2",
      public_display_name: "invalid\nname",
      public_category: "skill",
    },
  ]);

  assert.equal(visible[0]?.type, "tool");
  assert.equal(visible[0]?.type === "tool" ? visible[0].name : null, "QA Review");
  assert.equal(
    visible[0]?.type === "tool" ? visible[0].public_display_name : null,
    "QA Review",
  );
  assert.equal(visible[1]?.type === "tool" ? visible[1].name : null, "Read");
  assert.equal(
    visible[1]?.type === "tool" ? visible[1].public_display_name : undefined,
    undefined,
  );
  assert.equal(visible[2]?.type === "tool" ? visible[2].name : null, "Skill");
  assert.doesNotMatch(JSON.stringify(visible), /workspace|private-token|raw-/);
});

test("keeps only public subagent lifecycle and drops legacy sandbox state", () => {
  const publicSubagent: MessagePart = {
    type: "subagent",
    agent_id: "subagent-public-1",
    public_operation_id: "subagent-public-1",
    agent_name: "cat /workspace/private --token secret",
    input: "private prompt",
    result: "private result",
    error: "private error",
    status: "complete",
    depth: 1,
  };
  const legacySubagent: MessagePart = {
    ...publicSubagent,
    agent_id: "legacy-private-id",
    public_operation_id: undefined,
  };
  const unknownStatus = {
    ...publicSubagent,
    status: "waiting-for-private-result",
  } as unknown as MessagePart;
  const sandbox: MessagePart = {
    type: "sandbox",
    status: "ready",
    sandbox_id: "private-sandbox-id",
  };

  const visible = getVisibleMessageParts([
    legacySubagent,
    unknownStatus,
    sandbox,
    publicSubagent,
  ]);
  assert.deepEqual(visible, [{
    type: "subagent",
    agent_id: "subagent-public-1",
    public_operation_id: "subagent-public-1",
    agent_name: "Sub-agent",
    input: "",
    isPending: false,
    depth: 1,
    parts: [],
    startedAt: undefined,
    completedAt: undefined,
    status: "complete",
    parent_agent_id: undefined,
    duration_ms: undefined,
    progress_percent: undefined,
    current_category: undefined,
  }]);
  assert.doesNotMatch(JSON.stringify(visible), /prompt|result|error|private/);
});

test("hides routine intent, context, queue, and run-start transcript cards", () => {
  const parts: MessagePart[] = [
    {
      type: "run_status",
      event_id: "evt-context",
      event_type: "context_snapshot_created",
      stage: "context",
      message: "已记录运行上下文快照",
      severity: "info",
    },
    {
      type: "run_status",
      event_id: "evt-intent",
      event_type: "intent_detected",
      stage: "intent",
      message: "已识别处理方式",
      severity: "info",
    },
    {
      type: "run_status",
      event_id: "evt-queued",
      event_type: "queued",
      stage: "queue",
      message: "任务已进入队列",
      severity: "info",
    },
    {
      type: "run_status",
      event_id: "evt-run-started",
      event_type: "run_started",
      stage: "worker",
      message: "Run started",
      severity: "info",
    },
    {
      type: "run_status",
      event_id: "evt-skills",
      event_type: "skills_staged",
      stage: "skills",
      message: "Platform Skills staged for Claude Agent SDK",
      severity: "info",
    },
    {
      type: "text",
      content: "这是用户需要看到的回复。",
    },
  ];

  assert.deepEqual(
    getVisibleMessageParts(parts).map((part) => part.type),
    ["text"],
  );
});

test("groups only contiguous execution steps without rewriting ordered parts", () => {
  const firstStep: MessagePart = {
    type: "execution_step",
    sequence: 1,
    step_id: "step-first",
    kind: "processing",
    progress: { current: 1, total: 1 },
    status: "completed",
    safe_file_name: null,
  };
  const secondStep: MessagePart = {
    ...firstStep,
    sequence: 2,
    step_id: "step-second",
  };
  const thirdStep: MessagePart = {
    ...firstStep,
    sequence: 3,
    step_id: "step-third",
  };
  const parts: MessagePart[] = [
    firstStep,
    { type: "text", content: "正文" },
    secondStep,
    { type: "thinking", content: "公开思考", public_reasoning: true },
    thirdStep,
  ];

  const visible = getVisibleMessageParts(parts);
  assert.deepEqual(visible.map((part) => part.type), [
    "execution_process",
    "text",
    "execution_process",
  ]);
  assert.deepEqual(parts, [firstStep, parts[1], secondStep, parts[3], thirdStep]);
  assert.deepEqual(
    visible
      .filter((part): part is Extract<MessagePart, { type: "execution_process" }> => part.type === "execution_process")
      .map((part) => part.steps.map((step) => step.step_id)),
    [["step-first"], ["step-second", "step-third"]],
  );
});
test("keeps user-actionable run status cards visible", () => {
  const parts: MessagePart[] = [
    {
      type: "run_status",
      event_id: "evt-denied",
      event_type: "tool_denied",
      stage: "policy",
      message: "工具权限被拒绝",
      severity: "info",
    },
    {
      type: "run_status",
      event_id: "evt-warning",
      event_type: "tool_permission_required",
      stage: "policy",
      message: "工具调用需要权限决策",
      severity: "warning",
    },
    {
      type: "run_status",
      event_id: "evt-error",
      event_type: "run_failed",
      stage: "worker",
      message: "运行失败",
      severity: "error",
    },
  ];

  assert.deepEqual(
    getVisibleMessageParts(parts).map((part) => part.type),
    ["run_status", "run_status", "run_status"],
  );
});
