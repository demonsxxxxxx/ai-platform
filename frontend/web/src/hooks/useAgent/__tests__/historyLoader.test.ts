import assert from "node:assert/strict";
import test from "node:test";

import type { MessagePart } from "../../../types";
import {
  mergeHydratedAssistantRunSegment,
  reconstructMessagesFromEvents,
} from "../historyLoader.ts";
import type { HistoryEvent } from "../types.ts";

test("reconstructMessagesFromEvents preserves backend user message ids", () => {
  const messages = reconstructMessagesFromEvents(
    [
      {
        event_type: "user:message",
        run_id: "run-1",
        timestamp: "2026-05-08T00:00:00.000Z",
        data: {
          content: "fork from here",
          message_id: "user-message-1",
          attachments: [],
        },
      } satisfies HistoryEvent,
    ],
    new Set<string>(),
    { activeSubagentStack: [] },
  );

  assert.equal(messages.length, 1);
  assert.equal(messages[0]?.id, "user-message-1");
  assert.equal(messages[0]?.runId, "run-1");
});

test("production compatibility history reconstructs each persisted user turn before its run answer", () => {
  const messages = reconstructMessagesFromEvents(
    [
      {
        id: "msg-old-user",
        type: "user:message",
        event_type: "user:message",
        run_id: "run-old",
        timestamp: "2026-07-15T01:00:00.000Z",
        data: {
          message_id: "msg-old-user",
          run_id: "run-old",
          content: "第一轮问题",
        },
      },
      {
        id: "run-old:final",
        type: "message:chunk",
        event_type: "message:chunk",
        run_id: "run-old",
        timestamp: "2026-07-15T01:01:00.000Z",
        data: { run_id: "run-old", content: "第一轮回答" },
      },
      {
        id: "msg-new-user",
        type: "user:message",
        event_type: "user:message",
        run_id: "run-new",
        timestamp: "2026-07-15T02:00:00.000Z",
        data: {
          message_id: "msg-new-user",
          run_id: "run-new",
          content: "第二轮问题",
        },
      },
      {
        id: "run-new:final",
        type: "message:chunk",
        event_type: "message:chunk",
        run_id: "run-new",
        timestamp: "2026-07-15T02:01:00.000Z",
        data: { run_id: "run-new", content: "第二轮回答" },
      },
    ] satisfies HistoryEvent[],
    new Set<string>(),
    { activeSubagentStack: [] },
  );

  assert.deepEqual(
    messages.map((message) => [message.role, message.runId, message.content]),
    [
      ["user", "run-old", "第一轮问题"],
      ["assistant", "run-old", "第一轮回答"],
      ["user", "run-new", "第二轮问题"],
      ["assistant", "run-new", "第二轮回答"],
    ],
  );
});

test("reconstructMessagesFromEvents keeps overlapping runs in independent backend-ordered assistant segments", () => {
  const messages = reconstructMessagesFromEvents(
    [
      {
        id: "run-old:chunk",
        event_type: "message:chunk",
        run_id: "run-old",
        timestamp: "2026-07-15T03:00:00.000Z",
        data: { run_id: "run-old", content: "旧运行答案" },
      },
      {
        id: "run-old:artifact",
        event_type: "artifact_card",
        run_id: "run-old",
        timestamp: "2026-07-15T03:00:01.000Z",
        data: {
          run_id: "run-old",
          artifact_id: "artifact-old",
          artifact_type: "report",
          label: "旧运行产物",
          content_type: "text/plain",
          size_bytes: 10,
          download_url: "/api/ai/artifacts/artifact-old/download",
        },
      },
      {
        id: "run-new:chunk",
        event_type: "message:chunk",
        run_id: "run-new",
        timestamp: "2026-07-15T02:00:00.000Z",
        data: { run_id: "run-new", content: "新运行答案" },
      },
      {
        id: "run-new:terminal",
        event_type: "done",
        run_id: "run-new",
        timestamp: "2026-07-15T02:00:01.000Z",
        data: { run_id: "run-new", status: "succeeded" },
      },
    ] satisfies HistoryEvent[],
    new Set<string>(),
    { activeSubagentStack: [] },
  );

  const assistants = messages.filter((message) => message.role === "assistant");
  assert.deepEqual(assistants.map((message) => message.runId), ["run-old", "run-new"]);
  assert.equal(assistants[0]?.content, "旧运行答案");
  assert.equal(assistants[1]?.content, "新运行答案");
  assert.deepEqual(
    assistants.map((message) =>
      (message.parts || [])
        .filter((part) => part.type === "artifact")
        .map((part) => part.type === "artifact" && part.artifact_id),
    ),
    [["artifact-old"], []],
  );
});

test("exact terminal hydration replaces one run segment without duplicating or touching adjacent runs", () => {
  const existing = [
    {
      id: "assistant-old",
      role: "assistant" as const,
      runId: "run-old",
      content: "旧运行答案",
      timestamp: new Date("2026-07-15T01:00:00Z"),
      parts: [],
    },
    {
      id: "assistant-new-partial",
      role: "assistant" as const,
      runId: "run-new",
      content: "部分答案",
      timestamp: new Date("2026-07-15T02:00:00Z"),
      parts: [],
    },
    {
      id: "assistant-new-replay",
      role: "assistant" as const,
      runId: "run-new",
      content: "重复片段",
      timestamp: new Date("2026-07-15T02:00:01Z"),
      parts: [],
    },
  ];
  const hydrated = {
    id: "assistant-new-final",
    role: "assistant" as const,
    runId: "run-new",
    content: "完整答案",
    timestamp: new Date("2026-07-15T02:00:00Z"),
    parts: [],
  };

  const merged = mergeHydratedAssistantRunSegment(existing, hydrated);

  assert.deepEqual(merged.map((message) => message.runId), ["run-old", "run-new"]);
  assert.equal(merged[0]?.content, "旧运行答案");
  assert.equal(merged[1]?.content, "完整答案");
});

test("reconstructMessagesFromEvents treats timezone-less backend timestamps as UTC", () => {
  const originalTimezone = process.env.TZ;
  process.env.TZ = "Asia/Shanghai";
  try {
    const messages = reconstructMessagesFromEvents(
      [
        {
          event_type: "user:message",
          run_id: "run-1",
          timestamp: "2026-05-07T16:30:00.000",
          data: {
            content: "hello",
            message_id: "user-message-1",
            attachments: [],
          },
        } satisfies HistoryEvent,
      ],
      new Set<string>(),
      { activeSubagentStack: [] },
    );

    assert.equal(
      messages[0]?.timestamp.toISOString(),
      "2026-05-07T16:30:00.000Z",
    );
  } finally {
    process.env.TZ = originalTimezone;
  }
});

test("reconstructMessagesFromEvents keeps token usage after cancel on the cancelled assistant", () => {
  const messages = reconstructMessagesFromEvents(
    [
      {
        id: "event-user",
        event_type: "user:message",
        run_id: "run_20260516152217_bd0ba9a2",
        timestamp: "2026-05-16T15:22:17.793Z",
        data: {
          content: "创建一个 Python Hello World 脚本",
          message_id: "run_20260516152217_bd0ba9a2:user",
          run_id: "run_20260516152217_bd0ba9a2",
          attachments: [],
        },
      },
      {
        id: "event-sandbox-starting",
        event_type: "sandbox:starting",
        run_id: "run_20260516152217_bd0ba9a2",
        timestamp: "2026-05-16T15:22:18.961Z",
        data: {
          timestamp: "2026-05-16T15:22:18.961711+00:00",
          agent_id: "search",
        },
      },
      {
        id: "event-thinking",
        event_type: "thinking",
        run_id: "run_20260516152217_bd0ba9a2",
        timestamp: "2026-05-16T15:22:40.515Z",
        data: {
          content:
            "用户要求创建一个 Python Hello World 脚本。这是一个简单的任务。",
          thinking_id: "lc_run--019e3161-c59c-7ab2-a91d-7249e2216feb",
          agent_id: "search",
        },
      },
      {
        id: "event-token-empty",
        event_type: "token:usage",
        run_id: "run_20260516152217_bd0ba9a2",
        timestamp: "2026-05-16T15:22:43.422Z",
        data: {
          input_tokens: 0,
          output_tokens: 0,
          total_tokens: 0,
          duration: 0,
        },
      },
      {
        id: "event-cancel",
        event_type: "user:cancel",
        run_id: "run_20260516152217_bd0ba9a2",
        timestamp: "2026-05-16T15:22:43.445Z",
        data: {
          run_id: "run_20260516152217_bd0ba9a2",
        },
      },
      {
        id: "event-token-final",
        event_type: "token:usage",
        run_id: "run_20260516152217_bd0ba9a2",
        timestamp: "2026-05-16T15:22:43.732Z",
        data: {
          input_tokens: 15581,
          output_tokens: 68,
          total_tokens: 15649,
          duration: 24.927353858947754,
          model: "MiniMax-M2.7",
        },
      },
    ] satisfies HistoryEvent[],
    new Set<string>(),
    { activeSubagentStack: [] },
  );

  assert.equal(messages.length, 2);
  assert.equal(messages[0]?.role, "user");
  assert.equal(messages[1]?.role, "assistant");
  assert.equal(messages[1]?.cancelled, true);
  assert.equal(messages[1]?.tokenUsage?.total_tokens, 15649);
  assert.equal(messages[1]?.duration, 24927.353858947754);
});

test("reconstructMessagesFromEvents replays ai-platform run events and artifact cards", () => {
  const processedEventIds = new Set<string>();
  const messages = reconstructMessagesFromEvents(
    [
      {
        id: "event-user",
        event_type: "user:message",
        run_id: "run-review",
        timestamp: "2026-06-02T01:00:00.000Z",
        data: {
          content: "审核这个 Word",
          message_id: "run-review:user",
          attachments: [],
        },
      },
      {
        id: "event-tool-status",
        event_type: "run_event",
        run_id: "run-review",
        timestamp: "2026-06-02T01:00:01.000Z",
        data: {
          event_id: "evt-tool-status",
          event_type: "tool_permission_required",
          stage: "policy",
          message: "tool permission required",
          severity: "warning",
          sequence: 7,
          payload: {
            storage_key: "tenants/default/private/tool.json",
          },
        },
      },
      {
        id: "event-artifact-card",
        event_type: "artifact_card",
        run_id: "run-review",
        timestamp: "2026-06-02T01:00:02.000Z",
        data: {
          artifact_id: "art-reviewed",
          artifact_type: "reviewed_docx",
          label: "审核 Word",
          size_bytes: 123,
          download_url: "/api/ai/artifacts/art-reviewed/download",
          status: "available",
          manifest: {
            storage_key: "tenants/default/runs/run-review/artifacts/a.docx",
          },
        },
      },
    ] satisfies HistoryEvent[],
    processedEventIds,
    { activeSubagentStack: [] },
  );

  assert.equal(messages.length, 2);
  assert.equal(messages[1]?.role, "assistant");
  assert.deepEqual(messages[1]?.parts?.map((part) => part.type), [
    "run_status",
    "artifact",
  ]);
  assert.deepEqual([...processedEventIds], [
    "event-tool-status",
    "event-artifact-card",
  ]);
  assert.doesNotMatch(
    JSON.stringify(messages[1]?.parts),
    /storage_key|tenants\/default/,
  );
});

test("reconstructMessagesFromEvents accepts production outer event types and keeps final payloads before the synthetic terminal", () => {
  const messages = reconstructMessagesFromEvents(
    [
      {
        id: "persisted-progress",
        sequence: 41,
        event_type: "worker_started",
        run_id: "run-terminal",
        timestamp: "2026-07-15T01:00:00.000Z",
        data: {
          event_id: "persisted-progress",
          run_id: "run-terminal",
          event_type: "worker_started",
          stage: "worker",
          severity: "info",
          content: "开始处理",
        },
      },
      {
        id: "final-detail",
        event_type: "final_detail",
        run_id: "run-terminal",
        timestamp: "2026-07-15T01:00:01.000Z",
        data: { detail_kind: "failed", detail_code: "run_failed" },
      },
      {
        id: "artifact-card",
        event_type: "artifact_card",
        run_id: "run-terminal",
        timestamp: "2026-07-15T01:00:02.000Z",
        data: {
          artifact_id: "artifact-terminal",
          artifact_type: "report",
          label: "报告",
          size_bytes: 1,
          download_url: "/api/ai/artifacts/artifact-terminal/download",
        },
      },
      {
        id: "run-terminal:terminal:failed",
        event_type: "done",
        run_id: "run-terminal",
        timestamp: "2026-07-15T01:00:03.000Z",
        data: { run_id: "run-terminal", status: "failed" },
      },
    ] satisfies HistoryEvent[],
    new Set<string>(),
    { activeSubagentStack: [] },
  );

  assert.equal(messages.length, 1);
  assert.match(messages[0]?.content || "", /任务未能完成/);
  assert.deepEqual(messages[0]?.parts?.map((part) => part.type), [
    "artifact",
  ]);
});

test("reconstructMessagesFromEvents replays a production outer permission event through the compatibility envelope", () => {
  const messages = reconstructMessagesFromEvents(
    [
      {
        id: "outer-permission:user",
        event_type: "user:message",
        run_id: "run-outer-permission",
        timestamp: "2026-07-15T00:00:00Z",
        data: { content: "执行工具" },
      },
      {
        id: "outer-permission:request",
        sequence: 12,
        event_type: "tool_permission_requested",
        run_id: "run-outer-permission",
        timestamp: "2026-07-15T00:00:01Z",
        data: {
          event_id: "outer-permission:request",
          run_id: "run-outer-permission",
          event_type: "tool_permission_requested",
          tool_permission_card: {
            permission_request_id: "permission-outer",
            run_id: "run-outer-permission",
            tool_id: "Bash",
            tool_call_id: "call-outer",
            risk_level: "high",
            write_capable: true,
            status: "pending",
          },
        },
      },
    ] satisfies HistoryEvent[],
    new Set<string>(),
    { activeSubagentStack: [] },
  );

  const assistant = messages.find(
    (message) => message.role === "assistant" && message.runId === "run-outer-permission",
  );
  assert.equal(
    assistant?.parts?.some(
      (part) =>
        part.type === "tool_permission" &&
        part.permission_request_id === "permission-outer",
    ),
    true,
  );
});

test("reconstructMessagesFromEvents replays tool permission request and decision cards", () => {
  const processedEventIds = new Set<string>();
  const messages = reconstructMessagesFromEvents(
    [
      {
        id: "event-user",
        event_type: "user:message",
        run_id: "run-review",
        timestamp: "2026-06-02T01:00:00.000Z",
        data: {
          content: "审核这个 Word",
          message_id: "run-review:user",
          attachments: [],
        },
      },
      {
        id: "event-permission-requested",
        event_type: "run_event",
        run_id: "run-review",
        timestamp: "2026-06-02T01:00:01.000Z",
        data: {
          event_id: "evt-permission-requested",
          run_id: "run-review",
          event_type: "tool_permission_requested",
          stage: "tool_policy",
          message: "工具调用需要权限决策",
          severity: "warning",
          sequence: 8,
          payload: {
            permission_request_id: "tpr-a",
            tool_id: "ragflow-knowledge-search",
            tool_call_id: "call-a",
            risk_level: "high",
            write_capable: true,
            request_payload: {
              storage_key: "tenants/default/private/tool.json",
            },
          },
        },
      },
      {
        id: "event-permission-decided",
        event_type: "run_event",
        run_id: "run-review",
        timestamp: "2026-06-02T01:00:02.000Z",
        data: {
          event_id: "evt-permission-decided",
          run_id: "run-review",
          event_type: "tool_permission_decided",
          stage: "tool_policy",
          message: "工具权限已决策",
          sequence: 9,
          payload: {
            permission_request_id: "tpr-a",
            tool_id: "ragflow-knowledge-search",
            tool_call_id: "call-a",
            decision: "deny",
            decision_payload: {
              storage_key: "tenants/default/private/decision.json",
            },
          },
        },
      },
    ] satisfies HistoryEvent[],
    processedEventIds,
    { activeSubagentStack: [] },
  );

  assert.equal(messages.length, 2);
  assert.equal(messages[1]?.role, "assistant");
  assert.deepEqual(messages[1]?.parts?.map((part) => part.type), [
    "tool_permission",
  ]);
  const part = messages[1]?.parts?.[0] as MessagePart & {
    type: "tool_permission";
    status: string;
    decision: string;
  };
  assert.equal(part.type, "tool_permission");
  assert.equal(part.status, "decided");
  assert.equal(part.decision, "deny");
  assert.deepEqual([...processedEventIds], [
    "event-permission-requested",
    "event-permission-decided",
  ]);
  assert.doesNotMatch(
    JSON.stringify(messages[1]?.parts),
    /request_payload|decision_payload|storage_key|tenants\/default/,
  );
});

test("reconstructMessagesFromEvents replays public tool permission card projections", () => {
  const processedEventIds = new Set<string>();
  const messages = reconstructMessagesFromEvents(
    [
      {
        id: "event-user",
        event_type: "user:message",
        run_id: "run-review",
        timestamp: "2026-06-02T01:00:00.000Z",
        data: {
          content: "审核这个 Word",
          message_id: "run-review:user",
          attachments: [],
        },
      },
      {
        id: "event-permission-card",
        event_type: "run_event",
        run_id: "run-review",
        timestamp: "2026-06-02T01:00:01.000Z",
        data: {
          event_id: "evt-permission-card",
          run_id: "run-review",
          event_type: "tool_permission_card",
          stage: "tool_policy",
          message: "工具调用需要权限决策",
          severity: "warning",
          sequence: 8,
          payload: {
            tool_permission_card: {
              schema_version: "ai-platform.tool-permission-card.v1",
              permission_request_id: "tpr-card",
              run_id: "run-review",
              tool_id: "ragflow-knowledge-search",
              tool_call_id: "call-card",
              risk_level: "high",
              write_capable: true,
              status: "pending",
              decision_endpoint:
                "/api/ai/runs/run-review/tool-permissions/tpr-card/decision",
              request_payload: {
                storage_key: "tenants/default/private/tool.json",
              },
              command_sha256: "a".repeat(64),
            },
          },
        },
      },
      {
        id: "event-permission-card-decided",
        event_type: "run_event",
        run_id: "run-review",
        timestamp: "2026-06-02T01:00:02.000Z",
        data: {
          event_id: "evt-permission-card-decided",
          run_id: "run-review",
          event_type: "tool_permission_card",
          stage: "tool_policy",
          message: "工具权限已决策",
          sequence: 9,
          payload: {
            tool_permission_card: {
              schema_version: "ai-platform.tool-permission-card.v1",
              permission_request_id: "tpr-card",
              run_id: "run-review",
              tool_id: "ragflow-knowledge-search",
              tool_call_id: "call-card",
              risk_level: "high",
              write_capable: true,
              status: "decided",
              decision: "deny",
              decision_payload: {
                storage_key: "tenants/default/private/decision.json",
              },
              command_sha256: "b".repeat(64),
            },
          },
        },
      },
    ] satisfies HistoryEvent[],
    processedEventIds,
    { activeSubagentStack: [] },
  );

  assert.equal(messages.length, 2);
  assert.equal(messages[1]?.role, "assistant");
  assert.deepEqual(messages[1]?.parts?.map((part) => part.type), [
    "tool_permission",
  ]);
  const part = messages[1]?.parts?.[0] as MessagePart & {
    type: "tool_permission";
    status: string;
    decision: string;
  };
  assert.equal(part.type, "tool_permission");
  assert.equal(part.status, "decided");
  assert.equal(part.decision, "deny");
  assert.deepEqual([...processedEventIds], [
    "event-permission-card",
    "event-permission-card-decided",
  ]);
  assert.doesNotMatch(
    JSON.stringify(messages[1]?.parts),
    /request_payload|decision_payload|storage_key|command_sha256|tenants\/default/,
  );
});

test("reconstructMessagesFromEvents replays top-level public tool permission card events", () => {
  const processedEventIds = new Set<string>();
  const messages = reconstructMessagesFromEvents(
    [
      {
        id: "event-user",
        event_type: "user:message",
        run_id: "run-review",
        timestamp: "2026-06-02T01:00:00.000Z",
        data: {
          content: "审核这个 Word",
          message_id: "run-review:user",
          attachments: [],
        },
      },
      {
        id: "event-permission-card",
        event_type: "tool_permission_card",
        run_id: "run-review",
        timestamp: "2026-06-02T01:00:01.000Z",
        data: {
          event_id: "evt-permission-card",
          content: "工具调用需要权限决策",
          status: "tool_policy",
          tool_permission_card: {
            schema_version: "ai-platform.tool-permission-card.v1",
            permission_request_id: "tpr-card",
            run_id: "run-review",
            tool_id: "ragflow-knowledge-search",
            tool_call_id: "call-card",
            risk_level: "high",
            write_capable: true,
            status: "pending",
            request_payload: {
              storage_key: "tenants/default/private/tool.json",
            },
            command_sha256: "a".repeat(64),
          },
        },
      },
      {
        id: "event-permission-card-decided",
        event_type: "tool_permission_card",
        run_id: "run-review",
        timestamp: "2026-06-02T01:00:02.000Z",
        data: {
          event_id: "evt-permission-card-decided",
          content: "工具权限已决策",
          status: "tool_policy",
          tool_permission_card: {
            schema_version: "ai-platform.tool-permission-card.v1",
            permission_request_id: "tpr-card",
            run_id: "run-review",
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
      },
    ] satisfies HistoryEvent[],
    processedEventIds,
    { activeSubagentStack: [] },
  );

  assert.equal(messages.length, 2);
  assert.equal(messages[1]?.role, "assistant");
  assert.deepEqual(messages[1]?.parts?.map((part) => part.type), [
    "tool_permission",
  ]);
  const part = messages[1]?.parts?.[0] as MessagePart & {
    type: "tool_permission";
    status: string;
    decision: string;
  };
  assert.equal(part.type, "tool_permission");
  assert.equal(part.status, "decided");
  assert.equal(part.decision, "allow_once");
  assert.deepEqual([...processedEventIds], [
    "event-permission-card",
    "event-permission-card-decided",
  ]);
  assert.doesNotMatch(
    JSON.stringify(messages[1]?.parts),
    /request_payload|decision_payload|storage_key|command_sha256|tenants\/default/,
  );
});
