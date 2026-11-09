import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { createLobeServerTestRoot } from "../../../common/__tests__/lobeUiTestRoot";

import type { MessagePart } from "../../../../types";
import "../../../../i18n";
import { MessageWorkActivity } from "../MessageWorkActivity";

test("collapsed work activity keeps failed and denied operation counts visible", async () => {
  const renderToStaticMarkup = await createLobeServerTestRoot();
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
  assert.match(markup, /data-work-activity-failed-count="true">· 3 项失败/);
  assert.match(markup, /data-work-activity-denied-count="true">· 2 项未授权/);
  // The chevron follows the label; there is no separate right-aligned toggle text.
  assert.doesNotMatch(markup, /查看执行详情|>全部展开<|>全部收起</);
});

test("stopping work keeps its nonterminal status without an active spinner or completed claim", async () => {
  const renderToStaticMarkup = await createLobeServerTestRoot();
  const markup = renderToStaticMarkup(createElement(MessageWorkActivity, {
    messageId: "message-stopping", isStreaming: false, isStopping: true,
    parts: [{ type: "tool", name: "Read", args: {}, status: "started", public_operation_id: "stop-tool", public_category: "read" }],
    partKeys: ["stop-tool"], renderPart: () => createElement("span", null, "保留工具记录"),
  }));
  assert.match(markup, /data-status="stopping"/);
  assert.match(markup, /已请求取消/);
  assert.doesNotMatch(markup, /animate-spin|aui-shimmer|data-status="complete"/);
});

test("streaming hides routine phases and keeps only the latest meaningful phase", async () => {
  const renderToStaticMarkup = await createLobeServerTestRoot();
  const routineStarted: Extract<MessagePart, { type: "execution_step" }> = {
    type: "execution_step",
    sequence: 1,
    step_id: "phase_model_wait",
    stage: "model_wait",
    kind: "analysis",
    status: "running",
    progress: { current: 0, total: 1 },
    safe_file_name: null,
  };
  const routineCompleted: Extract<MessagePart, { type: "execution_step" }> = {
    ...routineStarted,
    sequence: 2,
    status: "completed",
    progress: { current: 1, total: 1 },
  };
  const meaningfulOld: Extract<MessagePart, { type: "execution_step" }> = {
    ...routineStarted,
    sequence: 3,
    step_id: "step-old",
    stage: "custom_work",
    title: "旧过程",
  };
  const meaningfulLatest: Extract<MessagePart, { type: "execution_step" }> = {
    ...meaningfulOld,
    sequence: 4,
    step_id: "step-latest",
    title: "最新过程",
  };
  const parts: MessagePart[] = [routineStarted, routineCompleted, meaningfulOld, meaningfulLatest];
  const markup = renderToStaticMarkup(
    createElement(MessageWorkActivity, {
      messageId: "message-phase-filter",
      isStreaming: true,
      parts,
      partKeys: parts.map((_, index) => `part-${index}`),
      renderPart: (part) => createElement("span", {
        "data-rendered-part": part.type,
        "data-rendered-text": part.type === "execution_step" ? part.title || part.step_id : undefined,
      }),
    }),
  );

  assert.doesNotMatch(markup, /phase_model_wait|model_wait/);
  assert.doesNotMatch(markup, /旧过程/);
  assert.match(markup, /最新过程/);
});

test("completed routine phases stay hidden while failed phases and cancelled tools remain visible", async () => {
  const renderToStaticMarkup = await createLobeServerTestRoot();
  const routine: Extract<MessagePart, { type: "execution_step" }> = {
    type: "execution_step",
    sequence: 1,
    step_id: "phase_skill_staging",
    stage: "skill_staging",
    kind: "capability",
    status: "completed",
    progress: { current: 1, total: 1 },
    safe_file_name: null,
  };
  const failed: Extract<MessagePart, { type: "execution_step" }> = {
    ...routine,
    sequence: 2,
    step_id: "phase_sandbox_submission",
    stage: "sandbox_submission",
    status: "failed",
    progress: { current: 0, total: 1 },
  };
  const cancelled: Extract<MessagePart, { type: "tool" }> = {
    type: "tool",
    name: "Execute",
    args: {},
    status: "started",
    cancelled: true,
    public_operation_id: "operation-cancelled",
    public_category: "execute",
  };
  const parts: MessagePart[] = [routine, failed, cancelled];
  const markup = renderToStaticMarkup(
    createElement(MessageWorkActivity, {
      messageId: "message-failure-preservation",
      isStreaming: false,
      parts,
      partKeys: parts.map((_, index) => `part-${index}`),
      renderPart: (part) => createElement("span", {
        "data-rendered-part": part.type,
        "data-rendered-text": part.type === "execution_step" ? part.title || part.step_id : part.type === "tool" ? part.public_operation_id : undefined,
      }),
    }),
  );

  assert.doesNotMatch(markup, /phase_skill_staging/);
  assert.match(markup, /phase_sandbox_submission/);
  assert.match(markup, /operation-cancelled/);
  assert.match(markup, /1 项失败/);
});

test("groups only adjacent successful tools and leaves failures as separate visible work", async () => {
  const renderToStaticMarkup = await createLobeServerTestRoot();
  const tool = (id: string, status: "completed" | "failed"): Extract<MessagePart, { type: "tool" }> => ({
    type: "tool",
    name: "Execute",
    args: {},
    status,
    public_operation_id: id,
    public_category: "execute",
  });
  const parts: MessagePart[] = [tool("operation-1", "completed"), tool("operation-2", "completed"), tool("operation-failed", "failed"), tool("operation-3", "completed")];
  const markup = renderToStaticMarkup(
    createElement(MessageWorkActivity, {
      messageId: "message-tool-groups",
      parts,
      partKeys: parts.map((_, index) => `part-${index}`),
      renderPart: (part) => createElement("span", { "data-rendered-operation": part.type === "tool" ? part.public_operation_id : undefined }, part.type),
    }),
  );

  assert.equal((markup.match(/data-tool-group/g) ?? []).length, 1);
  assert.match(markup, /已完成 2 项连续工具操作/);
  assert.match(markup, /operation-failed/);
  assert.match(markup, /operation-3/);
});

test("work process disclosure renders above the answer so streaming text grows below it", async () => {
  const renderToStaticMarkup = await createLobeServerTestRoot();
  const answer = {
    type: "text",
    content: "Final answer.",
    logical_id: "part-answer",
    public_part_id: "part-answer",
    text_role: "answer",
  } satisfies MessagePart;
  const work = {
    type: "summary",
    kind: "work_trace",
    content: "Checking public sources.",
  } satisfies MessagePart;
  const parts: MessagePart[] = [work, answer];
  const markup = renderToStaticMarkup(
    createElement(MessageWorkActivity, {
      messageId: "message-order",
      parts,
      partKeys: parts.map((_, index) => `part-${index}`),
      renderPart: (part) =>
        createElement("span", { "data-part": part.type }, part.type),
    }),
  );

  const answerIndex = markup.indexOf('data-part="text"');
  const toggleIndex = markup.indexOf("data-message-work-details-toggle");
  assert.ok(answerIndex >= 0, "expected the answer part to render");
  assert.ok(toggleIndex >= 0 && toggleIndex < answerIndex, "work disclosure must precede the answer");
  assert.match(markup, /data-message-work-activity-order="process-first"/);
  assert.doesNotMatch(markup, /result-first/);
});
