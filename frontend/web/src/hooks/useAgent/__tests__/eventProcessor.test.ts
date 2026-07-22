import assert from "node:assert/strict";
import test from "node:test";
import type { MessagePart } from "../../../types";
import { processMessageEvent } from "../eventProcessor.ts";
import { isAssistantTextProjection } from "../types.ts";

test("projects the controlled native Skill sandbox admission failure stage", () => {
  const result = processMessageEvent(
    "final_detail",
    {
      run_id: "run-native-failed",
      projection_version: "ai-platform.chat-public-projection.v1",
      detail_kind: "failed",
      detail_code: "skill_sandbox_admission_failed",
      message: "unsafe token at /home/private/runtime.log",
    },
    [],
    "",
    [],
    0,
    [],
    false,
    "run-native-failed",
  );

  assert.equal(result.content.length > 0, true);
  assert.equal(result.parts.length, 1);
  const part = result.parts[0];
  assert.equal(part?.type, "run_status");
  if (part?.type !== "run_status") throw new Error("expected run status");
  assert.equal(part.stage, "skill_sandbox_admission");
  assert.equal(part.event_type, "skill_sandbox_admission_failed");
  assert.equal(part.severity, "error");
  assert.match(part.message, /隔离沙箱准入/);
  assert.doesNotMatch(
    JSON.stringify(result),
    /native_tool_admission_failed|\/home\/|token/,
  );
});

test("keeps safe partial output and adds an actionable terminal failure", () => {
  const parts: MessagePart[] = [
    { type: "text", content: "已完成公开部分；" },
    {
      type: "run_status",
      event_id: "evt-progress",
      event_type: "agent_step_started",
      stage: "activity",
      message: "正在处理数据并准备结果",
      severity: "info",
    },
  ];
  const result = processMessageEvent(
    "final_detail",
    {
      run_id: "run-failed-partial",
      projection_version: "ai-platform.chat-public-projection.v1",
      detail_kind: "failed",
      detail_code: "model_service_unavailable",
      message: "raw provider token at /home/private/runtime.log",
    },
    parts,
    "已完成公开部分；",
    [],
    0,
    [],
    false,
    "run-failed-partial",
  );

  assert.equal(result.content, "已完成公开部分；");
  assert.deepEqual(result.parts.map((part) => part.type), [
    "text",
    "run_status",
    "run_status",
  ]);
  const terminal = result.parts.at(-1);
  assert.equal(terminal?.type, "run_status");
  if (terminal?.type !== "run_status") throw new Error("expected run status");
  assert.equal(terminal.event_type, "model_service_unavailable");
  assert.match(terminal.message, /模型服务暂时不可用/);
  assert.doesNotMatch(JSON.stringify(result), /provider token|\/home\/private/);
});

test("marks a cancelled terminal while retaining safe partial output", () => {
  const result = processMessageEvent(
    "final_detail",
    {
      run_id: "run-cancelled-partial",
      projection_version: "ai-platform.chat-public-projection.v1",
      detail_kind: "cancelled",
      detail_code: "run_cancelled",
      message: "untrusted cancellation detail /home/private/runtime.log",
    },
    [{ type: "text", content: "已生成部分结果" }],
    "已生成部分结果",
    [],
    0,
    [],
    false,
    "run-cancelled-partial",
  );

  assert.equal(result.content, "已生成部分结果");
  assert.equal(result.cancelled, true);
  assert.equal(result.parts.at(-1)?.type, "run_status");
  assert.doesNotMatch(JSON.stringify(result), /untrusted|\/home\/private/);
});

test("fails closed for unknown or mismatched terminal detail", () => {
  for (const data of [
    {
      detail_kind: "failed",
      detail_code: "private_executor_failure",
      message: "secret token at /home/private/runtime.log",
    },
    {
      detail_kind: "cancelled",
      detail_code: "run_timeout",
      message: "secret token at /home/private/runtime.log",
    },
  ]) {
    const result = processMessageEvent(
      "final_detail",
      data,
      [],
      "",
      [],
      0,
      [],
      false,
      "run-unknown",
    );
    assert.deepEqual(result.parts, []);
    assert.equal(result.content, "");
  }
});

test("merges streamed summary chunks inside a subagent by summary id", () => {
  let parts: MessagePart[] = [
    {
      type: "subagent",
      agent_id: "agent-1",
      agent_name: "Research",
      input: "look this up",
      depth: 1,
      isPending: true,
      status: "running",
      parts: [],
    },
  ];

  const first = processMessageEvent(
    "summary",
    { content: "first ", summary_id: "summary-1", agent_id: "agent-1" },
    parts,
    "",
    [],
    1,
    [{ agent_id: "agent-1", depth: 1, message_id: "message-1" }],
    true,
    "message-1",
  );
  parts = first.parts;

  const second = processMessageEvent(
    "summary",
    { content: "second", summary_id: "summary-1", agent_id: "agent-1" },
    parts,
    "",
    [],
    1,
    [{ agent_id: "agent-1", depth: 1, message_id: "message-1" }],
    true,
    "message-1",
  );

  const subagent = second.parts[0];
  assert.equal(subagent.type, "subagent");
  const summaries = subagent.parts?.filter((part) => part.type === "summary");

  assert.equal(summaries?.length, 1);
  assert.equal(summaries?.[0]?.content, "first second");
});

test("hides routine ai-platform run events from the chat transcript", () => {
  const result = processMessageEvent(
    "run_event",
    {
      event_id: "evt-context",
      sequence: 4,
      event_type: "context_snapshot_created",
      stage: "context",
      message: "已记录运行上下文快照",
      severity: "info",
      payload: {
        snapshot_id: "snapshot-a",
        storage_key: "tenants/default/private/tool.json",
      },
    } as never,
    [],
    "",
    [],
    0,
    [],
    true,
    "message-1",
  );

  assert.equal(result.parts.length, 0);
  assert.doesNotMatch(JSON.stringify(result), /storage_key|tenants\/default/);
});

test("rejects legacy public tool-log events in favor of commentary activities", () => {
  const result = processMessageEvent(
    "run_event",
    {
      projection_version: "ai-platform.chat-public-projection.v1",
      event_id: "evt-legacy-tool-log",
      sequence: 5,
      event_type: "tool_call_started",
      stage: "tool",
      message: "Bash python private-script.py --token secret",
      severity: "info",
    },
    [],
    "",
    [],
    0,
    [],
    true,
    "message-1",
  );

  assert.deepEqual(result.parts, []);
  assert.doesNotMatch(JSON.stringify(result), /Bash|python|token|private-script/);
});

test("keeps user-actionable ai-platform run warnings visible", () => {
  const result = processMessageEvent(
    "run_event",
    {
      projection_version: "ai-platform.chat-public-projection.v1",
      event_id: "evt-tool",
      sequence: 4,
      event_type: "agent_step_blocked",
      stage: "wait",
      message: "当前处理步骤未获授权，正在等待权限调整",
      severity: "warning",
      payload: {
        reason: "requires confirmation",
        storage_key: "tenants/default/private/tool.json",
      },
    } as never,
    [],
    "",
    [],
    0,
    [],
    true,
    "message-1",
  );

  assert.equal(result.parts.length, 1);
  const part = result.parts[0] as MessagePart & {
    type: "run_status";
    event_id: string;
    event_type: string;
    stage: string;
    message: string;
    severity: string;
    sequence: number;
  };
  assert.equal(part.type, "run_status");
  assert.equal(part.event_id, "evt-tool");
  assert.equal(part.event_type, "agent_step_blocked");
  assert.equal(part.stage, "wait");
  assert.equal(part.message, "当前处理步骤未获授权，正在等待权限调整");
  assert.equal(part.severity, "warning");
  assert.equal(part.sequence, 4);
  assert.doesNotMatch(JSON.stringify(part), /storage_key|tenants\/default/);
});

test("streams versioned assistant deltas and converges to one canonical final", () => {
  assert.equal(
    isAssistantTextProjection({
      projection_version: "ai-platform.chat-public-projection.v1",
      projection_kind: "assistant_final",
      content: "canonical",
    }),
    true,
  );
  const progressAndPartial: MessagePart[] = [
    {
      type: "run_status",
      event_id: "evt-progress",
      event_type: "run_started",
      stage: "status",
      message: "任务已开始处理",
      severity: "info",
    },
    { type: "text", content: "Hel" },
  ];

  const delta = processMessageEvent(
    "message:chunk",
    {
      projection_version: "ai-platform.chat-public-projection.v1",
      projection_kind: "assistant_delta",
      event_id: "evt-delta",
      sequence: 3,
      run_id: "run-a",
      content: "lo",
    },
    progressAndPartial,
    "Hel",
    [],
    0,
    [],
    true,
    "message-1",
  );

  assert.equal(delta.content, "Hello");
  assert.deepEqual(
    delta.parts.filter((part) => part.type === "text"),
    [{ type: "text", content: "Hello" }],
  );

  const final = processMessageEvent(
    "message:chunk",
    {
      projection_version: "ai-platform.chat-public-projection.v1",
      projection_kind: "assistant_final",
      run_id: "run-a",
      content: "Hello, world!",
    },
    [...delta.parts, { type: "text", content: " stale duplicate" }],
    delta.content,
    [],
    0,
    [],
    true,
    "message-1",
  );
  const replayedFinal = processMessageEvent(
    "message:chunk",
    {
      projection_version: "ai-platform.chat-public-projection.v1",
      projection_kind: "assistant_final",
      run_id: "run-a",
      content: "Hello, world!",
    },
    final.parts,
    final.content,
    [],
    0,
    [],
    false,
    "message-1",
  );

  assert.equal(replayedFinal.content, "Hello, world!");
  assert.deepEqual(replayedFinal.parts, [
    { type: "text", content: "Hello, world!" },
  ]);
});

test("shows only versioned allowlisted info progress in stream and history", () => {
  const internal = processMessageEvent(
    "run_event",
    {
      event_id: "evt-internal",
      sequence: 4,
      event_type: "run_started",
      stage: "status",
      message: "unversioned internal text",
      severity: "info",
    },
    [],
    "",
    [],
    0,
    [],
    false,
    "message-1",
  );
  assert.equal(internal.parts.length, 0);

  const started = processMessageEvent(
    "run_event",
    {
      projection_version: "ai-platform.chat-public-projection.v1",
      event_id: "evt-started",
      sequence: 5,
      event_type: "run_started",
      stage: "status",
      message: "任务已开始处理",
      severity: "info",
    },
    [],
    "",
    [],
    0,
    [],
    true,
    "message-1",
  );
  const waiting = processMessageEvent(
    "run_event",
    {
      projection_version: "ai-platform.chat-public-projection.v1",
      event_id: "evt-waiting",
      sequence: 6,
      event_type: "agent_step_blocked",
      stage: "wait",
      message: "正在等待前置步骤",
      severity: "info",
    },
    started.parts,
    "",
    [],
    0,
    [],
    false,
    "message-1",
  );

  assert.equal(waiting.parts.length, 2);
  assert.deepEqual(
    waiting.parts.map((part) =>
      part.type === "run_status" ? part.event_id : part.type,
    ),
    ["evt-started", "evt-waiting"],
  );
  assert.equal(
    waiting.parts[1]?.type === "run_status"
      ? waiting.parts[1].event_id
      : null,
    "evt-waiting",
  );
});

test("keeps a bounded public activity timeline and compacts repeated heartbeats", () => {
  let parts: MessagePart[] = [];
  for (let sequence = 1; sequence <= 14; sequence += 1) {
    const result = processMessageEvent(
      "run_event",
      {
        projection_version: "ai-platform.chat-public-projection.v1",
        event_id: `evt-${sequence}`,
        sequence,
        event_type: sequence % 2 === 0 ? "agent_step_started" : "run_started",
        stage: sequence % 2 === 0 ? "activity" : "execution",
        message: `公开活动 ${sequence}`,
        severity: "info",
      },
      parts,
      "",
      [],
      0,
      [],
      true,
      "message-1",
    );
    parts = result.parts;
  }
  assert.equal(parts.length, 12);
  assert.equal(
    parts[0]?.type === "run_status" ? parts[0].event_id : null,
    "evt-3",
  );

  const repeated = processMessageEvent(
    "run_event",
    {
      projection_version: "ai-platform.chat-public-projection.v1",
      event_id: "evt-15",
      sequence: 15,
      event_type: "agent_step_started",
      stage: "activity",
      message: "公开活动 14",
      severity: "info",
    },
    parts,
    "",
    [],
    0,
    [],
    true,
    "message-1",
  );
  assert.equal(repeated.parts.length, 12);
  const lastRepeated = repeated.parts.at(-1);
  assert.equal(
    lastRepeated?.type === "run_status" ? lastRepeated.event_id : null,
    "evt-15",
  );
});

test("projects tool permission request run events into a confirmation part with allowlisted fields", () => {
  const result = processMessageEvent(
    "run_event",
    {
      event_id: "evt-permission-requested",
      run_id: "run-a",
      sequence: 8,
      event_type: "tool_permission_requested",
      stage: "tool_policy",
      message: "工具调用需要权限决策",
      severity: "warning",
      payload: {
        visible_to_user: true,
        permission_request_id: "tpr-a",
        tool_id: "ragflow-knowledge-search",
        tool_call_id: "call-a",
        risk_level: "high",
        write_capable: true,
        request_payload: {
          storage_key: "tenants/default/private/tool.json",
        },
        storage_key: "tenants/default/private/tool.json",
      },
    } as never,
    [],
    "",
    [],
    0,
    [],
    true,
    "message-1",
  );

  assert.equal(result.parts.length, 1);
  const part = result.parts[0] as MessagePart & {
    type: "tool_permission";
    event_id: string;
    run_id: string;
    permission_request_id: string;
    tool_id: string;
    tool_call_id: string;
    risk_level: string;
    write_capable: boolean;
    status: string;
    sequence: number;
  };
  assert.equal(part.type, "tool_permission");
  assert.equal(part.event_id, "evt-permission-requested");
  assert.equal(part.run_id, "run-a");
  assert.equal(part.permission_request_id, "tpr-a");
  assert.equal(part.tool_id, "ragflow-knowledge-search");
  assert.equal(part.tool_call_id, "call-a");
  assert.equal(part.risk_level, "high");
  assert.equal(part.write_capable, true);
  assert.equal(part.status, "pending");
  assert.equal(part.sequence, 8);
  assert.doesNotMatch(
    JSON.stringify(part),
    /request_payload|storage_key|tenants\/default/,
  );
});

test("projects public tool permission card run events into a confirmation part", () => {
  const result = processMessageEvent(
    "run_event",
    {
      event_id: "evt-permission-card",
      run_id: "run-a",
      sequence: 10,
      event_type: "tool_permission_card",
      stage: "tool_policy",
      message: "工具调用需要权限决策",
      payload: {
        tool_permission_card: {
          schema_version: "ai-platform.tool-permission-card.v1",
          permission_request_id: "tpr-card",
          run_id: "run-a",
          tool_id: "ragflow-knowledge-search",
          tool_call_id: "call-card",
          action: "execute",
          risk_level: "high",
          write_capable: true,
          status: "pending",
          decision_endpoint:
            "/api/ai/runs/run-a/tool-permissions/tpr-card/decision",
          request_payload: {
            storage_key: "tenants/default/private/tool.json",
          },
          command_sha256: "a".repeat(64),
        },
      },
    } as never,
    [],
    "",
    [],
    0,
    [],
    true,
    "message-1",
  );

  assert.equal(result.parts.length, 1);
  const part = result.parts[0] as MessagePart & {
    type: "tool_permission";
    event_id: string;
    run_id: string;
    permission_request_id: string;
    tool_id: string;
    tool_call_id: string;
    risk_level: string;
    write_capable: boolean;
    status: string;
    sequence: number;
  };
  assert.equal(part.type, "tool_permission");
  assert.equal(part.event_id, "evt-permission-card");
  assert.equal(part.run_id, "run-a");
  assert.equal(part.permission_request_id, "tpr-card");
  assert.equal(part.tool_id, "ragflow-knowledge-search");
  assert.equal(part.tool_call_id, "call-card");
  assert.equal(part.risk_level, "high");
  assert.equal(part.write_capable, true);
  assert.equal(part.status, "pending");
  assert.equal(part.sequence, 10);
  assert.doesNotMatch(
    JSON.stringify(part),
    /request_payload|storage_key|command_sha256|tenants\/default/,
  );
});

test("projects top-level public tool permission card events into a confirmation part", () => {
  const result = processMessageEvent(
    "tool_permission_card",
    {
      event_id: "evt-history-card",
      run_id: "run-a",
      sequence: 12,
      content: "工具调用需要权限决策",
      status: "tool_policy",
      tool_permission_card: {
        schema_version: "ai-platform.tool-permission-card.v1",
        permission_request_id: "tpr-history",
        run_id: "run-a",
        tool_id: "ragflow-knowledge-search",
        tool_call_id: "call-history",
        risk_level: "high",
        write_capable: true,
        status: "pending",
        request_payload: {
          storage_key: "tenants/default/private/tool.json",
        },
        command_sha256: "a".repeat(64),
      },
    } as never,
    [],
    "",
    [],
    0,
    [],
    false,
    "message-1",
  );

  assert.equal(result.parts.length, 1);
  const part = result.parts[0] as MessagePart & {
    type: "tool_permission";
    permission_request_id: string;
    status: string;
  };
  assert.equal(part.type, "tool_permission");
  assert.equal(part.permission_request_id, "tpr-history");
  assert.equal(part.status, "pending");
  assert.doesNotMatch(
    JSON.stringify(part),
    /request_payload|storage_key|command_sha256|tenants\/default/,
  );
});

test("updates a tool permission confirmation part from decided run events", () => {
  const parts: MessagePart[] = [
    {
      type: "tool_permission",
      event_id: "evt-permission-requested",
      run_id: "run-a",
      permission_request_id: "tpr-a",
      tool_id: "ragflow-knowledge-search",
      tool_call_id: "call-a",
      risk_level: "high",
      write_capable: true,
      status: "pending",
      created_at: "2026-06-02T01:00:00.000Z",
    } as never,
  ];

  const result = processMessageEvent(
    "run_event",
    {
      event_id: "evt-permission-decided",
      run_id: "run-a",
      sequence: 9,
      event_type: "tool_permission_decided",
      stage: "tool_policy",
      message: "工具权限已决策",
      payload: {
        permission_request_id: "tpr-a",
        tool_id: "ragflow-knowledge-search",
        tool_call_id: "call-a",
        decision: "allow_once",
        decision_payload: {
          storage_key: "tenants/default/private/decision.json",
        },
      },
    } as never,
    parts,
    "",
    [],
    0,
    [],
    true,
    "message-1",
  );

  assert.equal(result.parts.length, 1);
  const part = result.parts[0] as MessagePart & {
    type: "tool_permission";
    status: string;
    decision: string;
    decided_event_id: string;
    sequence: number;
  };
  assert.equal(part.type, "tool_permission");
  assert.equal(part.status, "decided");
  assert.equal(part.decision, "allow_once");
  assert.equal(part.decided_event_id, "evt-permission-decided");
  assert.equal(part.sequence, 9);
  assert.doesNotMatch(
    JSON.stringify(part),
    /decision_payload|storage_key|tenants\/default/,
  );
});

test("closes the exact permission card from a terminalized run event", () => {
  const parts: MessagePart[] = [
    {
      type: "tool_permission",
      event_id: "evt-permission-requested",
      run_id: "run-a",
      permission_request_id: "tpr-terminal",
      tool_id: "Bash",
      tool_call_id: "call-terminal",
      risk_level: "high",
      write_capable: true,
      status: "pending",
    } as never,
  ];

  const result = processMessageEvent(
    "run_event",
    {
      event_id: "evt-permission-cancelled",
      run_id: "run-a",
      sequence: 10,
      event_type: "tool_permission_terminalized",
      stage: "tool_policy",
      payload: {
        permission_request_id: "tpr-terminal",
        tool_id: "Bash",
        tool_call_id: "call-terminal",
        action: "execute",
        risk_level: "high",
        write_capable: true,
        status: "cancelled",
        reason: "run_cancel_requested",
        decision_endpoint: "/api/ai/runs/run-a/tool-permissions/tpr-terminal/decision",
        decision_options: ["allow_once", "allow_for_run", "deny"],
      },
    } as never,
    parts,
    "",
    [],
    0,
    [],
    false,
    "message-1",
  );

  const part = result.parts[0] as MessagePart & {
    type: "tool_permission";
    status: string;
    permission_request_id: string;
  };
  assert.equal(part.type, "tool_permission");
  assert.equal(part.permission_request_id, "tpr-terminal");
  assert.equal(part.status, "cancelled");
  assert.doesNotMatch(JSON.stringify(part), /decision_endpoint|decision_options/);
});

test("does not reopen a terminalized permission card from a stale decision replay", () => {
  const parts: MessagePart[] = [
    {
      type: "tool_permission",
      event_id: "evt-permission-expired",
      run_id: "run-a",
      permission_request_id: "tpr-terminal-replay",
      tool_id: "Bash",
      tool_call_id: "call-terminal-replay",
      risk_level: "high",
      write_capable: true,
      status: "expired",
      sequence: 10,
    } as never,
  ];

  const result = processMessageEvent(
    "run_event",
    {
      event_id: "evt-permission-decided-stale",
      run_id: "run-a",
      sequence: 9,
      event_type: "tool_permission_decided",
      stage: "tool_policy",
      payload: {
        permission_request_id: "tpr-terminal-replay",
        tool_id: "Bash",
        tool_call_id: "call-terminal-replay",
        decision: "allow_once",
      },
    } as never,
    parts,
    "",
    [],
    0,
    [],
    true,
    "message-1",
  );

  const part = result.parts[0] as MessagePart & { type: "tool_permission"; status: string; sequence: number };
  assert.equal(part.type, "tool_permission");
  assert.equal(part.status, "expired");
  assert.equal(part.sequence, 10);
});

test("preserves pending tool risk fields when legacy decision events omit them", () => {
  const parts: MessagePart[] = [
    {
      type: "tool_permission",
      event_id: "evt-permission-requested",
      run_id: "run-a",
      permission_request_id: "tpr-a",
      tool_id: "ragflow-knowledge-search",
      tool_call_id: "call-a",
      risk_level: "high",
      write_capable: true,
      status: "pending",
      created_at: "2026-06-02T01:00:00.000Z",
    } as never,
  ];

  const result = processMessageEvent(
    "run_event",
    {
      event_id: "evt-permission-decided",
      run_id: "run-a",
      sequence: 9,
      event_type: "tool_permission_decided",
      stage: "tool_policy",
      message: "工具权限已决策",
      payload: {
        permission_request_id: "tpr-a",
        tool_id: "ragflow-knowledge-search",
        tool_call_id: "call-a",
        decision: "allow_once",
      },
    } as never,
    parts,
    "",
    [],
    0,
    [],
    true,
    "message-1",
  );

  const part = result.parts[0] as MessagePart & {
    type: "tool_permission";
    risk_level: string;
    write_capable: boolean;
    status: string;
  };
  assert.equal(part.type, "tool_permission");
  assert.equal(part.risk_level, "high");
  assert.equal(part.write_capable, true);
  assert.equal(part.status, "decided");
});

test("does not regress a decided tool permission part when pending replay arrives later", () => {
  const parts: MessagePart[] = [
    {
      type: "tool_permission",
      event_id: "evt-permission-card-decided",
      decided_event_id: "evt-permission-card-decided",
      run_id: "run-a",
      permission_request_id: "tpr-card",
      tool_id: "ragflow-knowledge-search",
      tool_call_id: "call-card",
      risk_level: "high",
      write_capable: true,
      status: "decided",
      decision: "allow_once",
      decided_at: "2026-06-02T01:00:01.000Z",
    } as never,
  ];

  const result = processMessageEvent(
    "tool_permission_card",
    {
      event_id: "evt-permission-card-pending",
      run_id: "run-a",
      sequence: 10,
      tool_permission_card: {
        schema_version: "ai-platform.tool-permission-card.v1",
        permission_request_id: "tpr-card",
        run_id: "run-a",
        tool_id: "ragflow-knowledge-search",
        tool_call_id: "call-card",
        risk_level: "high",
        write_capable: true,
        status: "pending",
      },
    } as never,
    parts,
    "",
    [],
    0,
    [],
    false,
    "message-1",
  );

  assert.equal(result.parts.length, 1);
  const part = result.parts[0] as MessagePart & {
    type: "tool_permission";
    status: string;
    decision: string;
  };
  assert.equal(part.type, "tool_permission");
  assert.equal(part.status, "decided");
  assert.equal(part.decision, "allow_once");
});

test("updates a public tool permission card from decided projection", () => {
  const parts: MessagePart[] = [
    {
      type: "tool_permission",
      event_id: "evt-permission-card",
      run_id: "run-a",
      permission_request_id: "tpr-card",
      tool_id: "ragflow-knowledge-search",
      tool_call_id: "call-card",
      risk_level: "high",
      write_capable: true,
      status: "pending",
      created_at: "2026-06-02T01:00:00.000Z",
    } as never,
  ];

  const result = processMessageEvent(
    "run_event",
    {
      event_id: "evt-permission-card-decided",
      run_id: "run-a",
      sequence: 11,
      event_type: "tool_permission_card",
      stage: "tool_policy",
      message: "工具权限已决策",
      payload: {
        tool_permission_card: {
          schema_version: "ai-platform.tool-permission-card.v1",
          permission_request_id: "tpr-card",
          run_id: "run-a",
          tool_id: "ragflow-knowledge-search",
          tool_call_id: "call-card",
          risk_level: "high",
          write_capable: true,
          status: "decided",
          decision: "allow_once",
          decision_payload: {
            storage_key: "tenants/default/private/decision.json",
          },
          command_sha256: "b".repeat(64),
        },
      },
    } as never,
    parts,
    "",
    [],
    0,
    [],
    true,
    "message-1",
  );

  assert.equal(result.parts.length, 1);
  const part = result.parts[0] as MessagePart & {
    type: "tool_permission";
    status: string;
    decision: string;
    decided_event_id: string;
    sequence: number;
  };
  assert.equal(part.type, "tool_permission");
  assert.equal(part.status, "decided");
  assert.equal(part.decision, "allow_once");
  assert.equal(part.decided_event_id, "evt-permission-card-decided");
  assert.equal(part.sequence, 11);
  assert.doesNotMatch(
    JSON.stringify(part),
    /decision_payload|storage_key|command_sha256|tenants\/default/,
  );
});

test("refreshes public safe fields when a decided tool permission card arrives", () => {
  const parts: MessagePart[] = [
    {
      type: "tool_permission",
      event_id: "evt-permission-card",
      run_id: "run-a",
      permission_request_id: "tpr-card",
      tool_id: "tool",
      tool_call_id: "call-card",
      risk_level: "low",
      write_capable: false,
      status: "pending",
    } as never,
  ];

  const result = processMessageEvent(
    "tool_permission_card",
    {
      event_id: "evt-permission-card-decided",
      run_id: "run-a",
      sequence: 11,
      tool_permission_card: {
        schema_version: "ai-platform.tool-permission-card.v1",
        permission_request_id: "tpr-card",
        run_id: "run-a",
        tool_id: "ragflow-knowledge-search",
        tool_call_id: "call-card",
        risk_level: "high",
        write_capable: true,
        status: "decided",
        decision: "allow_once",
      },
    } as never,
    parts,
    "",
    [],
    0,
    [],
    false,
    "message-1",
  );

  const part = result.parts[0] as MessagePart & {
    type: "tool_permission";
    tool_id: string;
    risk_level: string;
    write_capable: boolean;
    status: string;
  };
  assert.equal(part.type, "tool_permission");
  assert.equal(part.tool_id, "ragflow-knowledge-search");
  assert.equal(part.risk_level, "high");
  assert.equal(part.write_capable, true);
  assert.equal(part.status, "decided");
});

test("does not persist sandbox runtime work directories in message parts", () => {
  const result = processMessageEvent(
    "sandbox:ready",
    {
      sandbox_id: "sandbox-a",
      work_dir: "/tmp/tenants/default/runs/run-a/workspace",
      timestamp: "2026-06-02T01:00:00.000Z",
    },
    [],
    "",
    [],
    0,
    [],
    true,
    "message-1",
  );

  assert.equal(result.parts.length, 1);
  assert.equal(result.parts[0]?.type, "sandbox");
  assert.doesNotMatch(JSON.stringify(result.parts[0]), /work_dir|workspace/);
});

test("sanitizes legacy raw tool start events before storing message parts", () => {
  const result = processMessageEvent(
    "tool:start",
    {
      tool: "reveal_file",
      tool_call_id: "call-raw",
      args: {
        path: "docs/report.docx",
        storage_key: "tenants/default/private/tool.json",
        request_payload: {
          token: "hidden",
        },
        nested: {
          work_dir: "/workspace/.claude/runs/run-a",
          safe_label: "visible",
        },
        files: [
          {
            runtime_path: "/tmp/tenants/default/run-a/private.txt",
          },
          {
            label: "public",
          },
        ],
      },
    },
    [],
    "",
    [],
    0,
    [],
    true,
    "message-1",
  );

  const serializedParts = JSON.stringify(result.parts);
  const serializedCalls = JSON.stringify(result.toolCalls);

  assert.match(serializedParts, /docs\/report\.docx/);
  assert.match(serializedParts, /visible/);
  assert.match(serializedParts, /public/);
  assert.doesNotMatch(
    serializedParts,
    /storage_key|request_payload|work_dir|runtime_path|\.claude|tenants\/default\/private|\/tmp\/tenants/,
  );
  assert.doesNotMatch(
    serializedCalls,
    /storage_key|request_payload|work_dir|runtime_path|\.claude|tenants\/default\/private|\/tmp\/tenants/,
  );
});

test("sanitizes legacy raw tool result events before rendering output", () => {
  const parts: MessagePart[] = [
    {
      type: "tool",
      id: "call-raw",
      name: "execute",
      args: { command: "echo ok" },
      isPending: true,
    },
  ];

  const result = processMessageEvent(
    "tool:result",
    {
      tool: "execute",
      tool_call_id: "call-raw",
      success: true,
      result: {
        output: "ok",
        command_sha256: "abc123",
        storage_key: "tenants/default/private/result.json",
        nested: {
          runtime_path: "/tmp/tenants/default/run-a/result.txt",
          safe_count: 1,
        },
      },
    },
    parts,
    "",
    [],
    0,
    [],
    true,
    "message-1",
  );

  const serializedParts = JSON.stringify(result.parts);

  assert.match(serializedParts, /"output":"ok"/);
  assert.match(serializedParts, /"safe_count":1/);
  assert.doesNotMatch(
    serializedParts,
    /command_sha256|storage_key|runtime_path|tenants\/default\/private|\/tmp\/tenants/,
  );
});

test("sanitizes unknown diagnostics across chat error-bearing event parts", () => {
  const diagnostic = "C:\\private\\worker.log?token=secret <html>proxy</html>";
  const agentCall = processMessageEvent(
    "agent:call",
    { agent_id: "agent-safe", agent_name: "Safe Agent", input: "task" },
    [],
    "",
    [],
    0,
    [],
    true,
    "message-1",
  );
  const results = [
    processMessageEvent(
      "error",
      { error: diagnostic },
      [],
      "",
      [],
      0,
      [],
      true,
      "message-1",
    ),
    processMessageEvent(
      "sandbox:error",
      { error: diagnostic },
      [],
      "",
      [],
      0,
      [],
      true,
      "message-1",
    ),
    processMessageEvent(
      "tool:result",
      {
        tool: "execute",
        tool_call_id: "call-error",
        success: false,
        error: diagnostic,
        result: "",
      },
      [
        {
          type: "tool",
          id: "call-error",
          name: "execute",
          args: {},
          isPending: true,
        },
      ],
      "",
      [],
      0,
      [],
      true,
      "message-1",
    ),
    processMessageEvent(
      "agent:result",
      {
        agent_id: "agent-safe",
        success: false,
        result: "",
        error: diagnostic,
      },
      agentCall.parts,
      "",
      [],
      0,
      [],
      true,
      "message-1",
    ),
  ];

  for (const result of results) {
    const serialized = JSON.stringify(result);
    assert.doesNotMatch(
      serialized,
      /private|token|proxy|html|worker\.log/i,
    );
  }
});

test("dedupes ai-platform artifact cards by artifact id", () => {
  const first = processMessageEvent(
    "artifact_card",
    {
      artifact_id: "art-reviewed",
      artifact_type: "reviewed_docx",
      label: "审核 Word",
      content_type:
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      size_bytes: 123,
      download_url: "/api/ai/artifacts/art-reviewed/download",
      status: "available",
      manifest: {
        storage_key: "tenants/default/runs/run-a/artifacts/reviewed.docx",
      },
    } as never,
    [],
    "",
    [],
    0,
    [],
    true,
    "message-1",
  );

  const second = processMessageEvent(
    "artifact_card",
    {
      artifact_id: "art-reviewed",
      artifact_type: "reviewed_docx",
      label: "审核 Word",
      content_type:
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      size_bytes: 123,
      download_url: "/api/ai/artifacts/art-reviewed/download",
      status: "available",
    } as never,
    first.parts,
    "",
    [],
    0,
    [],
    true,
    "message-1",
  );

  assert.equal(second.parts.length, 1);
  const part = second.parts[0] as MessagePart & {
    type: "artifact";
    artifact_id: string;
    label: string;
    download_url: string;
    size_bytes: number;
  };
  assert.equal(part.type, "artifact");
  assert.equal(part.artifact_id, "art-reviewed");
  assert.equal(part.label, "审核 Word");
  assert.equal(part.download_url, "/api/ai/artifacts/art-reviewed/download");
  assert.equal(part.size_bytes, 123);
  assert.doesNotMatch(JSON.stringify(part), /storage_key|tenants\/default/);
});
