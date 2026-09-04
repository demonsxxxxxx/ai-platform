import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { MessagePart } from "../../../../types";
import { PUBLIC_TERMINAL_PRESENTATION_DEFINITIONS } from "../../../../hooks/useAgent/publicTerminalPresentation.ts";
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

test("renders public execution kind and status from the Chinese catalog instead of backend copy", async () => {
  const step: Extract<MessagePart, { type: "execution_step" }> = {
    type: "execution_step",
    sequence: 6,
    step_id: "step-prepare-report",
    kind: "processing",
    progress: { current: 4, total: 4 },
    status: "completed",
    safe_file_name: null,
  };
  const markup = renderToStaticMarkup(
    createElement(MessagePartRenderer, { part: step, isLast: true }),
  );

  assert.match(markup, /处理/);
  assert.match(markup, /已完成/);
  assert.match(markup, /role="status"/);
  assert.match(markup, /4\/4/);
  assert.match(markup, /data-public-execution-process/);
  assert.doesNotMatch(markup, /Backend says/);
  assert.doesNotMatch(markup, /rounded-lg|border-/);
  assert.doesNotMatch(markup, /tool|execute/i);

  const [startedKey] = createMessagePartRenderKeys("message-a", [
    {
      ...step,
      status: "running",
      progress: { current: 0, total: 4 },
    },
  ]);
  const [completedKey] = createMessagePartRenderKeys("message-a", [step]);
  assert.equal(startedKey, completedKey);
});

test("renders binary lifecycle as a status row without a progress bar", async () => {
  const markup = renderToStaticMarkup(
    createElement(MessagePartRenderer, {
      isLast: true,
      part: {
        type: "execution_step",
        sequence: 1,
        step_id: "step-binary",
        kind: "analysis",
        progress: { current: 0, total: 1 },
        status: "running",
        safe_file_name: null,
      } satisfies Extract<MessagePart, { type: "execution_step" }>,
    }),
  );
  assert.match(markup, /分析/);
  assert.match(markup, /进行中/);
  assert.doesNotMatch(markup, /role="progressbar"|0\/1|0%/);
  assert.doesNotMatch(markup, /Backend|backend-stage-copy|step-binary/);
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
  const markup = renderToStaticMarkup(
    createElement(MessagePartRenderer, { part, isLast: true }),
  );

  assert.match(markup, /执行已开始/);
  assert.match(markup, /进行中/);
  assert.doesNotMatch(markup, /Backend|backend execution stage/);

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
  assert.match(unknownMarkup, /执行状态更新/);
  assert.doesNotMatch(
    unknownMarkup,
    /private|token-bearing|stdout|C:\\private/i,
  );
});

test("renders every public terminal detail without exposing backend message or stage", () => {
  for (const [detailCode, definition] of Object.entries(
    PUBLIC_TERMINAL_PRESENTATION_DEFINITIONS,
  )) {
    const markup = renderToStaticMarkup(
      createElement(MessagePartRenderer, {
        part: {
          type: "run_status",
          event_id: `evt-${detailCode}`,
          event_type: detailCode,
          stage: "C:\\private\\runtime",
          message: "backend token-bearing private detail",
          severity: definition.severity,
        } satisfies Extract<MessagePart, { type: "run_status" }>,
        isLast: true,
      }),
    );

    assert.ok(markup.includes(definition.defaultEventLabel), detailCode);
    assert.ok(markup.includes(definition.defaultMessage), detailCode);
    assert.doesNotMatch(
      markup,
      /执行状态更新|backend|token-bearing|private|runtime/i,
      detailCode,
    );
  }
});

test("renders a validated reconciliation correlation ID without backend text", () => {
  const basePart = {
    type: "run_status",
    event_id: "evt-terminal-reconciliation",
    event_type: "terminal_reconciliation_failed",
    stage: "private repository stage",
    message: "backend exception with token",
    severity: "error",
  } satisfies Extract<MessagePart, { type: "run_status" }>;
  const markup = renderToStaticMarkup(
    createElement(MessagePartRenderer, {
      part: { ...basePart, run_reference: "run-correlation-123" },
      isLast: true,
    }),
  );

  assert.match(markup, /任务编号：run-correlation-123/);
  assert.doesNotMatch(markup, /backend exception|token|repository stage/i);

  const invalidMarkup = renderToStaticMarkup(
    createElement(MessagePartRenderer, {
      part: { ...basePart, run_reference: "<private-run>" },
      isLast: true,
    }),
  );
  assert.doesNotMatch(invalidMarkup, /private-run|任务编号：/i);
});

test("renders password-protected PDF guidance instead of a generic failure", () => {
  const markup = renderToStaticMarkup(
    createElement(MessagePartRenderer, {
      part: {
        type: "run_status",
        event_id: "evt-pdf-password",
        event_type: "context_file_pdf_password_required",
        stage: "private parser path",
        message: "PdfReadError at /runtime/private.pdf",
        severity: "error",
      } satisfies Extract<MessagePart, { type: "run_status" }>,
      isLast: true,
    }),
  );

  assert.match(markup, /PDF 文件需要密码/);
  assert.match(markup, /请先解除密码保护后重新上传/);
  assert.doesNotMatch(markup, /执行状态更新|PdfReadError|runtime|private/i);
});

test("renders a specific safe file-size failure instead of a generic failure", () => {
  const part: Extract<MessagePart, { type: "run_status" }> = {
    type: "run_status",
    event_id: "evt-file-too-large",
    event_type: "context_file_too_large",
    stage: "private storage stage",
    message: "private token-bearing backend detail",
    severity: "error",
  };
  const markup = renderToStaticMarkup(
    createElement(MessagePartRenderer, { part, isLast: true }),
  );

  assert.match(markup, /文件超过处理上限/);
  assert.match(markup, /文件超过 128 MB，或文件总量超过 256 MB/);
  assert.doesNotMatch(markup, /private|token-bearing|storage stage/);
});
