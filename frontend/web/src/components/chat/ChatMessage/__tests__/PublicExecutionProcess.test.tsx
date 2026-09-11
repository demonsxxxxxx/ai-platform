import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { ExecutionTimelinePart } from "../../../../types/message.ts";
import "../../../../i18n/index.ts";
import { PublicExecutionProcess } from "../PublicExecutionProcess.tsx";

test("renders one expandable generic process summary without private execution fields", async () => {
  const steps = [
    {
      type: "execution_step",
      step_id: "private-step-id",
      sequence: 2,
      kind: "processing",
      status: "completed",
      progress: { current: 2, total: 2 },
      safe_file_name: "report.xlsx",
      stage: "private-stage",
      event_id: "evt-private",
      run_id: "run-private",
      command: "rm -rf private",
      stdout: "secret stdout",
    },
    {
      type: "execution_step",
      step_id: "second-private-step-id",
      sequence: 3,
      kind: "verification",
      status: "failed",
      progress: { current: 1, total: 1 },
      safe_file_name: "C:\\private\\report.xlsx",
      reasoning: "private reasoning",
    },
  ] as unknown as ExecutionTimelinePart[];

  const markup = renderToStaticMarkup(
    createElement(PublicExecutionProcess, {
      steps,
      isStreaming: false,
      elapsedMs: 125_000,
    }),
  );

  assert.equal((markup.match(/data-public-execution-process/g) || []).length, 1);
  assert.match(markup, /<details/);
  assert.doesNotMatch(markup, /<details[^>]*\bopen(?:=|\s|>)/);
  assert.match(markup, /<summary/);
  assert.match(markup, /处理过程/);
  assert.match(markup, /处理/);
  assert.match(markup, /验证/);
  assert.match(markup, /耗时 2 分钟 5 秒/);
  assert.match(markup, /report\.xlsx/);
  assert.doesNotMatch(
    markup,
    /private-step-id|private-stage|evt-private|run-private|rm -rf|stdout|reasoning|C:\\private/i,
  );
});


test("keeps active execution rows visibly expanded", () => {
  const markup = renderToStaticMarkup(
    createElement(PublicExecutionProcess, {
      steps: [
        {
          type: "execution_step",
          step_id: "active-step",
          sequence: 1,
          kind: "processing",
          status: "running",
          progress: { current: 0, total: 1 },
          safe_file_name: null,
          started_at: "2026-08-27T00:00:00.000Z",
        },
      ],
      isStreaming: true,
      expandable: false,
    }),
  );

  assert.doesNotMatch(markup, /<details/);
  assert.match(markup, /处理/);
  assert.match(markup, /进行中/);
});
