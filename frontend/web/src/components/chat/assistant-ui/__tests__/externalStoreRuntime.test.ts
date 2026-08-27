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


test("external message conversion preserves only authorized public tool summaries", () => {
  const converted = toAssistantUiMessage({
    id: "message-1",
    role: "assistant",
    content: "",
    timestamp: new Date("2026-01-01T00:00:00Z"),
    parts: [{ type: "tool", id: "operation-1", name: "Search authorized sources", args: { category: "search", summary: "Query: stability evidence" }, result: "Search authorized sources completed", public_operation_id: "operation-1", public_category: "search", public_input_summary: "Query: stability evidence" }],
  });
  assert.deepEqual(converted.content, [{
    type: "tool-call",
    toolCallId: "operation-1",
    toolName: "Search authorized sources",
    args: { category: "search", summary: "Query: stability evidence" },
    argsText: "Query: stability evidence",
    isError: false,
    data: {
      inputSummary: "Query: stability evidence",
      resultSummary: "Search authorized sources completed",
    },
  }]);
});

test("external message conversion hides legacy tool identifiers and results", () => {
  const converted = toAssistantUiMessage({
    id: "message-legacy",
    role: "assistant",
    content: "",
    timestamp: new Date("2026-01-01T00:00:00Z"),
    parts: [{ type: "tool", id: "private-operation", name: "private_skill", args: {}, result: "secret output" }],
  });
  assert.deepEqual(converted.content, [{
    type: "tool-call",
    toolCallId: "tool-0",
    toolName: "Tool",
    args: {},
    argsText: "",
    isError: false,
  }]);
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
      agent_name: "Verification agent",
      input: "",
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
      name: "Verification agent",
      parentId: "agent-root",
      status: "running",
      depth: 1,
    },
  }]);
});

test("external message conversion keeps stable ids and hides thinking payload", () => {
  const converted = toAssistantUiMessage({
    id: "message-1",
    role: "assistant",
    content: "answer",
    timestamp: new Date("2026-01-01T00:00:00Z"),
    parts: [
      { type: "thinking", content: "private thinking", isStreaming: true },
      { type: "text", content: "answer" },
    ],
  });
  assert.equal(converted.id, "message-1");
  assert.deepEqual(converted.content, [
    { type: "reasoning", text: "", status: { type: "running" } },
    { type: "text", text: "answer" },
  ]);
});

test("external message conversion exposes only fixed public thinking summaries", () => {
  const converted = toAssistantUiMessage({
    id: "message-public-thinking",
    role: "assistant",
    content: "",
    timestamp: new Date("2026-01-01T00:00:00Z"),
    parts: [
      { type: "thinking", content: "Analyzing the request", isStreaming: true },
      { type: "thinking", content: "Analysis step completed", isStreaming: false },
      { type: "thinking", content: "Unreviewed reasoning", isStreaming: false },
    ],
  });

  assert.deepEqual(converted.content, [
    { type: "reasoning", text: "Analyzing the request", status: { type: "running" } },
    { type: "reasoning", text: "Analysis step completed", status: { type: "complete" } },
    { type: "reasoning", text: "", status: { type: "complete" } },
  ]);
});
