import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { MessagePart } from "../../../../types";
import i18n, { PRODUCT_LANGUAGE } from "../../../../i18n/index.ts";
import {
  createMessagePartRenderKeys,
  MessagePartRenderer,
} from "../MessagePartRenderer.tsx";

test("keeps streaming text object identity stable without using mutable content as a key", () => {
  const streamingText = {
    type: "text",
    content: "first token",
  } as MessagePart;
  const firstKey = createMessagePartRenderKeys("message-a", [streamingText])[0];
  (streamingText as Extract<MessagePart, { type: "text" }>).content =
    "first token and second token";
  const secondKey = createMessagePartRenderKeys("message-a", [streamingText])[0];

  assert.equal(firstKey, secondKey);
  assert.doesNotMatch(secondKey, /first token|second token/);
});

test("renders public execution kind and status from frontend i18n instead of backend copy", async () => {
  const step: Extract<MessagePart, { type: "execution_step" }> = {
    type: "execution_step",
    schema_version: "ai-platform.public-execution-event.v1",
    event_id: "evt-step-completed",
    sequence: 6,
    run_id: "run-execution",
    step_id: "step-prepare-report",
    kind: "processing",
    stage: "prepare",
    title: "Backend says Prepare report",
    summary: "Backend says Input is ready",
    progress: { current: 4, total: 4 },
    status: "completed",
    safe_file_name: null,
    artifact_public_id: null,
    created_at: null,
  };
  await i18n.changeLanguage("zh");
  const zhMarkup = renderToStaticMarkup(
    createElement(MessagePartRenderer, { part: step, isLast: true }),
  );
  await i18n.changeLanguage("en");
  const enMarkup = renderToStaticMarkup(
    createElement(MessagePartRenderer, { part: step, isLast: true }),
  );
  await i18n.changeLanguage(PRODUCT_LANGUAGE);

  assert.match(zhMarkup, /处理/);
  assert.match(zhMarkup, /已完成/);
  assert.match(enMarkup, /Processing/);
  assert.match(enMarkup, /Completed/);
  for (const markup of [zhMarkup, enMarkup]) {
    assert.match(markup, /role="status"/);
    assert.match(markup, /100%/);
    assert.match(markup, /data-execution-status="completed"/);
    assert.match(markup, /data-execution-step-id="step-prepare-report"/);
    assert.match(markup, /role="progressbar"/);
    assert.doesNotMatch(markup, /Backend says/);
    assert.doesNotMatch(markup, /rounded-lg|border-/);
    assert.doesNotMatch(markup, /tool|execute/i);
  }

  const [startedKey] = createMessagePartRenderKeys("message-a", [
    {
      ...step,
      event_id: "evt-step-started",
      status: "running",
      progress: { current: 0, total: 4 },
    },
  ]);
  const [completedKey] = createMessagePartRenderKeys("message-a", [step]);
  assert.equal(startedKey, completedKey);
});

test("renders binary lifecycle as a status row without a progress bar", async () => {
  await i18n.changeLanguage("en");
  const markup = renderToStaticMarkup(
    createElement(MessagePartRenderer, {
      isLast: true,
      part: {
        type: "execution_step",
        schema_version: "ai-platform.public-execution-event.v1",
        event_id: "evt-binary",
        sequence: 1,
        run_id: "run-binary",
        step_id: "step-binary",
        kind: "analysis",
        stage: "backend-stage-copy",
        title: "Backend title must not render",
        summary: "Backend summary must not render",
        progress: { current: 0, total: 1 },
        status: "running",
        safe_file_name: null,
        artifact_public_id: null,
        created_at: null,
      } satisfies Extract<MessagePart, { type: "execution_step" }>,
    }),
  );
  await i18n.changeLanguage(PRODUCT_LANGUAGE);

  assert.match(markup, /Analysis/);
  assert.match(markup, /Running/);
  assert.doesNotMatch(markup, /role="progressbar"|0\/1|0%/);
  assert.doesNotMatch(markup, /Backend|backend-stage-copy/);
});

test("renders run status from allowlisted event type instead of backend message and stage", async () => {
  const part: Extract<MessagePart, { type: "run_status" }> = {
    type: "run_status",
    event_id: "evt-run-started",
    event_type: "run_started",
    stage: "backend execution stage",
    message: "Backend says run started",
    severity: "info",
  };
  await i18n.changeLanguage("zh");
  const zhMarkup = renderToStaticMarkup(
    createElement(MessagePartRenderer, { part, isLast: true }),
  );
  await i18n.changeLanguage("en");
  const enMarkup = renderToStaticMarkup(
    createElement(MessagePartRenderer, { part, isLast: true }),
  );
  await i18n.changeLanguage(PRODUCT_LANGUAGE);

  assert.match(zhMarkup, /执行已开始/);
  assert.match(zhMarkup, /进行中/);
  assert.match(enMarkup, /Execution started/);
  assert.match(enMarkup, /Running/);
  assert.doesNotMatch(zhMarkup, /Backend|backend execution stage/);
  assert.doesNotMatch(enMarkup, /Backend|backend execution stage/);

  await i18n.changeLanguage("en");
  const unknownMarkup = renderToStaticMarkup(
    createElement(MessagePartRenderer, {
      part: {
        ...part,
        event_type: "private:token-bearing-event",
        stage: "C:\\private\\runtime",
        message: "stdout contains a private identifier",
      },
      isLast: true,
    }),
  );
  await i18n.changeLanguage(PRODUCT_LANGUAGE);
  assert.match(unknownMarkup, /Execution update/);
  assert.doesNotMatch(
    unknownMarkup,
    /private|token-bearing|stdout|C:\\private/i,
  );
});
