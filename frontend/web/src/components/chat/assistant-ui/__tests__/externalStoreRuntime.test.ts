import test from "node:test";
import assert from "node:assert/strict";
import { appendContent, toAssistantUiMessage } from "../externalStoreRuntime";
import type { AppendMessage } from "@assistant-ui/react";

test("assistant-ui composer delegates only text content to the existing send owner", () => {
  assert.equal(appendContent({ role: "user", content: "hello" } as unknown as AppendMessage), "hello");
  assert.equal(
    appendContent({
      role: "user",
      content: [
        { type: "text", text: "hello " },
        { type: "text", text: "world" },
      ],
    } as unknown as AppendMessage),
    "hello world",
  );
});

test("external message conversion keeps assistant-only status off user messages", () => {
  const user = toAssistantUiMessage({
    id: "message-user",
    role: "user",
    content: "hello",
    timestamp: new Date("2026-01-01T00:00:00Z"),
  });
  const assistant = toAssistantUiMessage({
    id: "message-assistant",
    role: "assistant",
    content: "hello",
    timestamp: new Date("2026-01-01T00:00:00Z"),
  });

  assert.equal("status" in user, false);
  assert.deepEqual(assistant.status, { type: "complete", reason: "stop" });
});


test("external message conversion preserves only authorized public tool metadata", () => {
  const converted = toAssistantUiMessage({
    id: "message-1",
    role: "assistant",
    content: "",
    timestamp: new Date("2026-01-01T00:00:00Z"),
    parts: [{
      type: "tool",
      id: "operation-1",
      name: "Bash: cat /workspace/private --token secret",
      args: {},
      status: "completed",
      public_operation_id: "operation-1",
      public_category: "search",
      duration_ms: 1200,
    }],
  });
  assert.deepEqual(converted.content, [{
    type: "tool-call",
    toolCallId: "operation-1",
    toolName: "Search",
    args: {},
    argsText: "",
    isError: false,
    data: {
      category: "search",
      durationMs: 1200,
    },
  }]);
});

test("external message conversion displays the authorized Skill name", () => {
  const converted = toAssistantUiMessage({
    id: "message-skill",
    role: "assistant",
    content: "",
    timestamp: new Date("2026-01-01T00:00:00Z"),
    parts: [{
      type: "tool",
      id: "operation-skill-1",
      name: "raw-skill-name /workspace/private",
      args: { command: "cat private-token" },
      result: "private result",
      status: "completed",
      public_operation_id: "operation-skill-1",
      public_display_name: "QA Review",
      public_category: "skill",
    }],
  });

  assert.equal(
    typeof converted.content === "string"
      ? null
      : converted.content[0]?.type === "tool-call"
        ? converted.content[0].toolName
        : null,
    "QA Review",
  );
  assert.doesNotMatch(
    JSON.stringify(converted),
    /raw-skill-name|workspace|private-token|private result/,
  );
});

test("external message conversion drops legacy and unknown tool parts", () => {
  const converted = toAssistantUiMessage({
    id: "message-legacy",
    role: "assistant",
    content: "",
    timestamp: new Date("2026-01-01T00:00:00Z"),
    parts: [
      {
        type: "tool",
        id: "private-operation",
        name: "private_skill",
        args: { command: "cat /workspace/private --token secret" },
        result: "secret output",
      },
      {
        type: "tool",
        id: "unknown-operation",
        name: "Unknown operation",
        args: {},
        status: "completed",
        public_operation_id: "unknown-operation",
        public_category: "future-private-category",
      },
      {
        type: "tool",
        id: "malformed-operation",
        name: "Malformed operation",
        args: {},
        status: "completed",
        public_operation_id: "../../private-operation",
        public_category: "read",
      },
      {
        type: "tool",
        id: "missing-status",
        name: "Missing status",
        args: {},
        public_operation_id: "operation-missing-status",
        public_category: "read",
      },
    ],
  });
  assert.equal(converted.content, "");
  assert.doesNotMatch(
    JSON.stringify(converted),
    /private_skill|secret|unknown-operation|malformed-operation|missing-status/,
  );
});

test("external message conversion preserves public subagent identity and parent grouping metadata", () => {
  const converted = toAssistantUiMessage({
    id: "message-1",
    role: "assistant",
    content: "",
    timestamp: new Date("2026-01-01T00:00:00Z"),
    parts: [{
      type: "subagent",
      agent_id: "agent-child",
      agent_name: "cat /workspace/private --token secret",
      input: "private prompt with /workspace/path",
      result: "private subagent result",
      error: "private subagent error",
      depth: 1,
      status: "running",
      isPending: true,
      parts: [],
      public_operation_id: "agent-child",
      parent_agent_id: "agent-root",
    }],
  });
  assert.deepEqual(converted.content, [{
    type: "data-subagent",
    data: {
      id: "agent-child",
      name: "Sub-agent",
      parentId: "agent-root",
      status: "running",
      depth: 1,
    },
  }]);
  assert.doesNotMatch(
    JSON.stringify(converted),
    /private prompt|workspace|secret|subagent result|subagent error/,
  );
});

test("external message conversion drops legacy subagent and sandbox state", () => {
  const converted = toAssistantUiMessage({
    id: "message-lifecycle",
    role: "assistant",
    content: "",
    timestamp: new Date("2026-01-01T00:00:00Z"),
    parts: [
      {
        type: "subagent",
        agent_id: "private-agent-id",
        agent_name: "private_worker",
        input: "private prompt",
        result: "private result",
        status: "complete",
        depth: 1,
      },
      {
        type: "sandbox",
        status: "error",
        sandbox_id: "private-sandbox-id",
        error: "private sandbox error",
      },
    ],
  });

  assert.equal(converted.content, "");
  assert.doesNotMatch(JSON.stringify(converted), /private|sandbox/);
});

test("external message conversion drops thinking parts", () => {
  const converted = toAssistantUiMessage({
    id: "message-1",
    role: "assistant",
    content: "answer",
    timestamp: new Date("2026-01-01T00:00:00Z"),
    parts: [
      { type: "thinking", content: "private model reasoning", isStreaming: true },
      { type: "text", content: "answer" },
    ],
  });
  assert.equal(converted.id, "message-1");
  assert.deepEqual(converted.content, [{ type: "text", text: "answer" }]);
});

test("external message conversion drops messages containing only thinking parts", () => {
  const converted = toAssistantUiMessage({
    id: "message-public-thinking",
    role: "assistant",
    content: "",
    timestamp: new Date("2026-01-01T00:00:00Z"),
    parts: [
      {
        type: "thinking",
        content: "private model reasoning",
        public_reasoning: true,
        isStreaming: true,
      },
      {
        type: "thinking",
        content: "another private block",
        public_reasoning: true,
        isStreaming: false,
      },
    ],
  });

  assert.equal(converted.content, "");
});
