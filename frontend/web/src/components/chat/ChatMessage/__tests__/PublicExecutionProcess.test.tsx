import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { ExecutionTimelinePart } from "../../../../types/message.ts";
import i18n, { PRODUCT_LANGUAGE } from "../../../../i18n/index.ts";
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
      title: "do not render title",
      summary: "do not render summary",
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

  await i18n.changeLanguage("en");
  const markup = renderToStaticMarkup(
    createElement(PublicExecutionProcess, { steps, isStreaming: false }),
  );
  await i18n.changeLanguage(PRODUCT_LANGUAGE);

  assert.equal((markup.match(/data-public-execution-process/g) || []).length, 1);
  assert.match(markup, /<details/);
  assert.match(markup, /Process/);
  assert.match(markup, /Processing/);
  assert.match(markup, /Verification/);
  assert.match(markup, /report\.xlsx/);
  assert.doesNotMatch(
    markup,
    /private-step-id|private-stage|evt-private|run-private|rm -rf|stdout|reasoning|C:\\private|do not render/i,
  );
});
