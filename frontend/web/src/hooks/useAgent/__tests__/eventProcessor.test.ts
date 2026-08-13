import assert from "node:assert/strict";
import test from "node:test";
import { getVisibleMessageParts } from "../../../components/chat/ChatMessage/messagePartVisibility.ts";
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

test("projects an actionable bounded file-size terminal without backend detail", () => {
  const result = processMessageEvent(
    "final_detail",
    {
      run_id: "run-file-too-large",
      projection_version: "ai-platform.chat-public-projection.v1",
      detail_kind: "failed",
      detail_code: "context_file_too_large",
      message: "private storage path and token",
    },
    [],
    "",
    [],
    0,
    [],
    false,
    "run-file-too-large",
  );

  assert.equal(result.content, "文件超过 32 MB 处理上限，请选择更小的文件后重试。");
  const terminal = result.parts[0];
  assert.equal(terminal?.type, "run_status");
  if (terminal?.type !== "run_status") throw new Error("expected run status");
  assert.equal(terminal.event_type, "context_file_too_large");
  assert.equal(terminal.stage, "file_preprocessing");
  assert.doesNotMatch(JSON.stringify(result), /private|storage path|token/);
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

test("projects turn-limit exhaustion as a safe actionable terminal", () => {
  const result = processMessageEvent(
    "final_detail",
    {
      run_id: "run-turn-limit",
      projection_version: "ai-platform.chat-public-projection.v1",
      detail_kind: "failed",
      detail_code: "run_budget_exhausted",
      message: "Reached maximum number of turns (128)",
    },
    [],
    "",
    [],
    0,
    [],
    false,
    "run-turn-limit",
  );

  assert.equal(result.content, "任务已达到执行轮次上限。请缩小或拆分任务后重试。");
  assert.equal(result.parts.length, 1);
  const terminal = result.parts[0];
  assert.equal(terminal?.type, "run_status");
  if (terminal?.type !== "run_status") throw new Error("expected run status");
  assert.equal(terminal.event_type, "run_budget_exhausted");
  assert.equal(terminal.severity, "error");
  assert.equal(terminal.message, "任务已达到执行轮次上限。请缩小或拆分任务后重试。");
  assert.doesNotMatch(JSON.stringify(result), /Reached maximum number of turns|128/);
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
  const visibleParts = getVisibleMessageParts(result.parts);
  assert.deepEqual(
    visibleParts.map((part) => part.type),
    ["text", "run_status"],
  );
  const terminal = visibleParts.at(-1);
  assert.equal(terminal?.type, "run_status");
  if (terminal?.type !== "run_status") throw new Error("expected run status");
  assert.equal(terminal.event_type, "run_cancelled");
  assert.equal(terminal.severity, "warning");
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

test("caps only routine info commentary and never evicts actionable status", () => {
  let parts: MessagePart[] = [];
  for (const event of [
    {
      event_id: "evt-failed",
      sequence: 1,
      event_type: "agent_step_failed",
      stage: "activity",
      message: "当前计划步骤未完成，正在整理可操作错误",
      severity: "error",
    },
    {
      event_id: "evt-blocked",
      sequence: 2,
      event_type: "agent_step_blocked",
      stage: "wait",
      message: "当前计划步骤正在等待前置条件",
      severity: "info",
    },
    {
      event_id: "evt-warning",
      sequence: 3,
      event_type: "agent_step_started",
      stage: "activity",
      message: "当前步骤需要用户处理",
      severity: "warning",
    },
  ]) {
    parts = processMessageEvent(
      "run_event",
      {
        projection_version: "ai-platform.chat-public-projection.v1",
        ...event,
      },
      parts,
      "",
      [],
      0,
      [],
      true,
      "message-1",
    ).parts;
  }
  for (let sequence = 4; sequence <= 17; sequence += 1) {
    parts = processMessageEvent(
      "run_event",
      {
        projection_version: "ai-platform.chat-public-projection.v1",
        event_id: `evt-info-${sequence}`,
        sequence,
        event_type: sequence % 2 === 0 ? "agent_step_started" : "run_started",
        stage: sequence % 2 === 0 ? "activity" : "execution",
        message: `普通说明 ${sequence}`,
        severity: "info",
      },
      parts,
      "",
      [],
      0,
      [],
      true,
      "message-1",
    ).parts;
  }

  const statusParts = parts.filter((part) => part.type === "run_status");
  const eventIds = statusParts.map((part) => part.event_id);
  assert.equal(statusParts.length, 15);
  assert.equal(
    statusParts.filter((part) => part.severity === "info" && !part.event_type.includes("blocked"))
      .length,
    12,
  );
  assert.equal(eventIds.includes("evt-failed"), true);
  assert.equal(eventIds.includes("evt-blocked"), true);
  assert.equal(eventIds.includes("evt-warning"), true);
  assert.equal(eventIds.includes("evt-info-4"), false);
  assert.equal(eventIds.includes("evt-info-5"), false);
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

test("rejects legacy raw tool start events from ordinary chat", () => {
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

  assert.deepEqual(result.parts, []);
  assert.deepEqual(result.toolCalls, []);
  assert.doesNotMatch(JSON.stringify(result), /docs\/report|visible|public/);
});

test("rejects legacy raw tool result events from ordinary chat", () => {
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
    [],
    "",
    [],
    0,
    [],
    true,
    "message-1",
  );

  assert.deepEqual(result.parts, []);
  assert.equal(result.toolResult, undefined);
  assert.doesNotMatch(JSON.stringify(result), /output|safe_count|command_sha256|storage_key/);
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

test("upserts strict public execution steps by step id without merging them into assistant text", () => {
  const started = processMessageEvent(
    "execution_step",
    {
      schema_version: "ai-platform.public-execution-event.v1",
      event_id: "evt-step-started",
      run_id: "run-execution",
      sequence: 4,
      step_id: "step-prepare-report",
      kind: "processing",
      stage: "prepare",
      status: "running",
      title: "准备报告",
      summary: "正在读取已批准的输入",
      progress: { current: 0, total: 4 },
      safe_file_name: null,
      artifact_public_id: null,
      created_at: null,
    } as never,
    [{ type: "text", content: "最终答复保持独立。" }],
    "最终答复保持独立。",
    [],
    0,
    [],
    true,
    "message-execution",
  );
  const progressed = processMessageEvent(
    "execution_progress",
    {
      schema_version: "ai-platform.public-execution-event.v1",
      event_id: "evt-step-progress",
      run_id: "run-execution",
      sequence: 5,
      step_id: "step-prepare-report",
      kind: "processing",
      stage: "prepare",
      status: "running",
      title: "准备报告",
      summary: "已读取已批准的输入",
      progress: { current: 2, total: 4 },
      safe_file_name: null,
      artifact_public_id: null,
      created_at: null,
    } as never,
    started.parts,
    started.content,
    [],
    0,
    [],
    true,
    "message-execution",
  );
  const completed = processMessageEvent(
    "execution_step_completed",
    {
      schema_version: "ai-platform.public-execution-event.v1",
      event_id: "evt-step-completed",
      run_id: "run-execution",
      sequence: 6,
      step_id: "step-prepare-report",
      kind: "processing",
      stage: "prepare",
      status: "completed",
      title: "准备报告",
      summary: "输入已准备完成",
      progress: { current: 4, total: 4 },
      safe_file_name: "report.docx",
      artifact_public_id: "artifact-public-report",
      created_at: "2026-07-27T08:00:00.000Z",
    } as never,
    progressed.parts,
    progressed.content,
    [],
    0,
    [],
    true,
    "message-execution",
  );

  assert.equal(completed.content, "最终答复保持独立。");
  assert.equal(completed.parts.length, 2);
  const executionStep = completed.parts[1] as {
    type: string;
    step_id: string;
    kind: string;
    progress: { current: number; total: number };
    status: string;
    safe_file_name: string | null;
  };
  assert.equal(executionStep.type, "execution_step");
  assert.equal(executionStep.step_id, "step-prepare-report");
  assert.equal(executionStep.kind, "processing");
  assert.deepEqual(executionStep.progress, { current: 4, total: 4 });
  assert.equal(executionStep.status, "completed");
  assert.equal(executionStep.safe_file_name, "report.docx");
  assert.doesNotMatch(
    JSON.stringify(completed.parts),
    /evt-step|run-execution|准备报告|输入已准备|artifact-public|2026-07-27/,
  );
});

test("collapses terminal public execution state without changing answer or artifact siblings", () => {
  const parts: MessagePart[] = [
    { type: "text", content: "公开答复" },
    {
      type: "execution_step",
      sequence: 2,
      step_id: "step-one",
      kind: "processing",
      status: "completed",
      progress: { current: 1, total: 1 },
      safe_file_name: null,
    },
    {
      type: "artifact",
      artifact_id: "artifact-public",
      artifact_type: "document",
      label: "report.docx",
      content_type: "application/octet-stream",
      size_bytes: 1,
    },
    {
      type: "execution_step",
      sequence: 3,
      step_id: "step-two",
      kind: "verification",
      status: "completed",
      progress: { current: 1, total: 1 },
      safe_file_name: "report.docx",
    },
  ];

  const terminal = processMessageEvent(
    "execution_step",
    {},
    parts,
    "公开答复",
    [],
    0,
    [],
    false,
    "message-terminal",
  );

  assert.deepEqual(terminal.parts.map((part) => part.type), [
    "text",
    "execution_process",
    "artifact",
  ]);
  const process = terminal.parts[1];
  assert.equal(process?.type, "execution_process");
  if (process?.type !== "execution_process") {
    throw new Error("expected public execution process");
  }
  assert.deepEqual(process.steps.map((step) => step.step_id), [
    "step-one",
    "step-two",
  ]);
  assert.equal(terminal.content, "公开答复");
});

test("fails closed when a history envelope carries a raw execution field", () => {
  const result = processMessageEvent(
    "run_event",
    {
      schema_version: "ai-platform.public-execution-event.v1",
      event_id: "evt-history-raw",
      run_id: "run-history-raw",
      sequence: 7,
      event_type: "execution_step",
      timestamp: "2026-07-31T01:00:00.000Z",
      step_id: "step-history-raw",
      kind: "processing",
      stage: "prepare",
      status: "running",
      title: "private title",
      summary: "private summary",
      progress: { current: 0, total: 1 },
      safe_file_name: null,
      artifact_public_id: null,
      created_at: null,
      command: "private command must fail closed",
    } as never,
    [],
    "",
    [],
    0,
    [],
    false,
    "assistant-history-raw",
  );

  assert.deepEqual(result.parts, []);
  assert.equal(result.content, "");
});

test("drops a path-like safe_file_name before public execution state is retained", () => {
  const result = processMessageEvent(
    "execution_step",
    {
      schema_version: "ai-platform.public-execution-event.v1",
      event_id: "evt-unsafe-file-name",
      run_id: "run-unsafe-file-name",
      sequence: 1,
      step_id: "step-unsafe-file-name",
      kind: "processing",
      stage: "prepare",
      status: "running",
      title: "private title",
      summary: "private summary",
      progress: { current: 0, total: 1 },
      safe_file_name: "C:\\private\\report.xlsx",
      artifact_public_id: null,
      created_at: null,
    } as never,
    [],
    "",
    [],
    0,
    [],
    true,
    "assistant-unsafe-file-name",
  );

  const step = result.parts[0];
  assert.equal(step?.type, "execution_step");
  if (step?.type !== "execution_step") throw new Error("expected execution step");
  assert.equal(step.safe_file_name, null);
  assert.doesNotMatch(JSON.stringify(result), /C:\\private/);
});

test("fails closed for malformed, unknown, or step-id-less public execution events", () => {
  for (const [eventType, data] of [
    [
      "execution_step",
      {
        schema_version: "ai-platform.public-execution-event.v1",
        event_id: "evt-without-step-id",
        sequence: 4,
        run_id: "run-execution",
        kind: "processing",
        stage: "prepare",
        status: "running",
        title: "准备报告",
        summary: "缺少步骤标识",
        progress: { current: 0, total: 4 },
      },
    ],
    [
      "execution_progress",
      {
        schema_version: "ai-platform.public-execution-event.v1",
        event_id: "evt-extra-content",
        sequence: 5,
        run_id: "run-execution",
        step_id: "step-prepare-report",
        kind: "processing",
        stage: "prepare",
        status: "running",
        title: "准备报告",
        summary: "额外字段不得显示",
        progress: { current: 2, total: 4 },
        content: "assistant text is not an execution event field",
      },
    ],
    [
      "execution_step_unknown",
      {
        schema_version: "ai-platform.public-execution-event.v1",
        event_id: "evt-unknown-step-event",
        sequence: 6,
        run_id: "run-execution",
        step_id: "step-prepare-report",
        kind: "processing",
        stage: "prepare",
        status: "running",
        title: "准备报告",
        summary: "未知事件不得显示",
        progress: { current: 0, total: 4 },
      },
    ],
    [
      "execution_progress",
      {
        schema_version: "ai-platform.public-execution-event.v1",
        event_id: "evt-numeric-progress",
        sequence: 7,
        run_id: "run-execution",
        step_id: "step-prepare-report",
        kind: "processing",
        stage: "prepare",
        status: "running",
        title: "准备报告",
        summary: "数值进度不得显示",
        progress: 2,
      },
    ],
  ] as const) {
    const result = processMessageEvent(
      eventType,
      data as never,
      [],
      "",
      [],
      0,
      [],
      true,
      "message-execution",
    );
    assert.deepEqual(result.parts, []);
    assert.equal(result.content, "");
  }
});
