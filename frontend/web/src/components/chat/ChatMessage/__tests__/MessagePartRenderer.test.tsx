import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { MessagePart } from "../../../../types";
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

test("renders a completed public execution step as a timeline row instead of a tool card", () => {
  const step: Extract<MessagePart, { type: "execution_step" }> = {
    type: "execution_step",
    schema_version: "ai-platform.public-execution-event.v1",
    event_id: "evt-step-completed",
    sequence: 6,
    run_id: "run-execution",
    step_id: "step-prepare-report",
    kind: "processing",
    stage: "prepare",
    title: "准备报告",
    summary: "输入已准备完成",
    progress: { current: 4, total: 4 },
    status: "completed",
    safe_file_name: null,
    artifact_public_id: null,
    created_at: null,
  };
  const markup = renderToStaticMarkup(
    createElement(MessagePartRenderer, {
      part: step,
      isLast: true,
    }),
  );

  assert.match(markup, /role="status"/);
  assert.match(markup, /准备报告/);
  assert.match(markup, /输入已准备完成/);
  assert.match(markup, /100%/);
  assert.match(markup, /data-execution-status="completed"/);
  assert.match(markup, /data-execution-step-id="step-prepare-report"/);
  assert.match(markup, /role="progressbar"/);
  assert.doesNotMatch(markup, /rounded-lg|border-/);
  assert.doesNotMatch(markup, /tool|execute/i);

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
