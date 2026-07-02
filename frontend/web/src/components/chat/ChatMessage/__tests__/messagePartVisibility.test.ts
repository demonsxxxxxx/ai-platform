import assert from "node:assert/strict";
import test from "node:test";

import type { MessagePart } from "../../../../types";
import { getVisibleMessageParts } from "../messagePartVisibility.ts";

test("hides routine run status cards from chat message rendering", () => {
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
      event_id: "evt-worker",
      event_type: "worker_started",
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
