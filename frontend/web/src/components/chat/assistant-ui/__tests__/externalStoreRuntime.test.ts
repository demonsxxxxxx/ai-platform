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
