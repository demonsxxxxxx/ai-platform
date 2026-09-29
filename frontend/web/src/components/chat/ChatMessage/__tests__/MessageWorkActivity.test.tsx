import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import type { MessagePart } from "../../../../types";
import "../../../../i18n";
import { MessageWorkActivity } from "../MessageWorkActivity";

test("collapsed work activity keeps failed and denied operation counts visible", () => {
  const failedTool: Extract<MessagePart, { type: "tool" }> = {
    type: "tool",
    name: "Read",
    args: {},
    status: "failed",
    public_operation_id: "operation-read-1",
    public_category: "read",
  };
  const deniedTool: Extract<MessagePart, { type: "tool" }> = {
    ...failedTool,
    status: "denied",
    public_operation_id: "operation-read-2",
  };
  const failedSubagent: Extract<MessagePart, { type: "subagent" }> = {
    type: "subagent",
    agent_id: "subagent-1",
    public_operation_id: "subagent-1",
    agent_name: "Sub-agent",
    input: "",
    depth: 1,
    status: "error",
    parts: [deniedTool],
  };
  const failedStep: Extract<MessagePart, { type: "execution_step" }> = {
    type: "execution_step",
    sequence: 1,
    step_id: "step-1",
    kind: "processing",
    status: "failed",
    progress: { current: 0, total: 1 },
    safe_file_name: null,
  };
  const parts: MessagePart[] = [
    failedTool,
    deniedTool,
    failedSubagent,
    { type: "execution_process", steps: [failedStep] },
  ];
  const markup = renderToStaticMarkup(
    createElement(MessageWorkActivity, {
      messageId: "message-1",
      parts,
      partKeys: parts.map((_, index) => `part-${index}`),
      renderPart: (part) => createElement("span", null, part.type),
    }),
  );

  assert.match(markup, /aria-expanded="false"/);
  assert.match(markup, /data-work-activity-failed-count="true">局部失败 3/);
  assert.match(markup, /data-work-activity-denied-count="true">未授权 2/);
  assert.match(markup, /查看执行详情/);
});
