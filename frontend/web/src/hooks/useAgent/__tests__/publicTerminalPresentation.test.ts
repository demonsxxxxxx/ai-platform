import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import { processMessageEvent } from "../eventProcessor.ts";
import { reconstructMessagesFromEvents } from "../historyLoader.ts";
import { PUBLIC_TERMINAL_PRESENTATION_DEFINITIONS } from "../publicTerminalPresentation.ts";
import type { HistoryEvent } from "../types.ts";

test("Chinese locale has complete event and detail text for every public terminal presentation", () => {
  const messages = JSON.parse(
    readFileSync(
      resolve(process.cwd(), "src/i18n/locales/zh.json"),
      "utf8",
    ),
  );
  for (const [detailCode, definition] of Object.entries(
    PUBLIC_TERMINAL_PRESENTATION_DEFINITIONS,
  )) {
    const messageKey = definition.messageKey.split(".").at(-1);
    const eventKey = definition.eventLabelKey.split(".").at(-1);
    assert.equal(
      messages.chat.runTerminal[messageKey ?? ""],
      definition.defaultMessage,
      `${detailCode} detail`,
    );
    assert.equal(
      messages.chat.runStatus.event[eventKey ?? ""],
      definition.defaultEventLabel,
      `${detailCode} event`,
    );
  }
});

test("live and replay projection use every fixed safe public terminal presentation", () => {
  for (const [detailCode, definition] of Object.entries(
    PUBLIC_TERMINAL_PRESENTATION_DEFINITIONS,
  )) {
    const result = processMessageEvent(
      "final_detail",
      {
        run_id: `run-${detailCode}`,
        projection_version: "ai-platform.chat-public-projection.v1",
        detail_kind: definition.detailKind,
        detail_code: detailCode,
        message: "private token at C:\\runtime\\secret.log",
      },
      [],
      "",
      [],
      0,
      [],
      false,
      `run-${detailCode}`,
    );

    const terminal = result.parts[0];
    if (terminal?.type !== "run_status") {
      throw new Error(`expected run status for ${detailCode}`);
    }
    assert.equal(terminal.event_type, detailCode);
    assert.equal(terminal.stage, definition.stage);
    assert.equal(terminal.severity, definition.severity);
    assert.equal(terminal.message, definition.defaultMessage);
    assert.equal(
      terminal.run_reference,
      detailCode === "terminal_reconciliation_failed"
        ? `run-${detailCode}`
        : undefined,
    );
    assert.doesNotMatch(
      JSON.stringify(result),
      /private token|runtime|secret\.log/i,
      detailCode,
    );
  }

  const fallback = processMessageEvent(
    "final_detail",
    {
      run_id: "run-unknown",
      projection_version: "ai-platform.chat-public-projection.v1",
      detail_kind: "failed",
      detail_code: "private_backend_failure",
      message: "private token at C:\\runtime\\secret.log",
    },
    [],
    "",
    [],
    0,
    [],
    false,
    "run-unknown",
  );
  assert.deepEqual(fallback.parts, []);
  assert.equal(fallback.content, "");
  assert.doesNotMatch(JSON.stringify(fallback), /private|runtime|secret\.log/i);
});

test("projection failure reason is rendered only from the fixed allowlist", () => {
  const definition =
    PUBLIC_TERMINAL_PRESENTATION_DEFINITIONS.claude_agent_sdk_public_projection_failed;
  const live = processMessageEvent(
    "final_detail",
    {
      run_id: "run-projection",
      projection_version: "ai-platform.chat-public-projection.v1",
      detail_kind: "failed",
      detail_code: "claude_agent_sdk_public_projection_failed",
      projection_failure_reason: "terminal_text_mismatch",
      message: "private SDK error at C:\\runtime\\secret.log",
    },
    [],
    "",
    [],
    0,
    [],
    false,
    "run-projection",
  );
  assert.match(live.content, /terminal_text_mismatch/);
  assert.doesNotMatch(JSON.stringify(live), /private SDK|runtime|secret\.log/i);

  const unknown = processMessageEvent(
    "final_detail",
    {
      run_id: "run-projection-unknown",
      projection_version: "ai-platform.chat-public-projection.v1",
      detail_kind: "failed",
      detail_code: "claude_agent_sdk_public_projection_failed",
      projection_failure_reason: "C:/runtime/private/secret.log",
    },
    [],
    "",
    [],
    0,
    [],
    false,
    "run-projection-unknown",
  );
  assert.equal(unknown.content, definition.defaultMessage);
  assert.doesNotMatch(JSON.stringify(unknown), /runtime|private|secret\.log/i);

  const replay = reconstructMessagesFromEvents(
    [
      {
        id: "evt-projection",
        event_type: "final_detail",
        run_id: "run-projection-replay",
        timestamp: "2026-08-20T01:00:00.000Z",
        data: {
          run_id: "run-projection-replay",
          projection_version: "ai-platform.chat-public-projection.v1",
          detail_kind: "failed",
          detail_code: "claude_agent_sdk_public_projection_failed",
          projection_failure_reason: "answer_too_large",
        },
      } satisfies HistoryEvent,
    ],
    new Set<string>(),
    { activeSubagentStack: [] },
  );
  assert.match(
    replay.find((message) => message.role === "assistant")?.content ?? "",
    /answer_too_large/,
  );
});

test("historical reconstruction preserves the actionable PDF-password cause", () => {
  const messages = reconstructMessagesFromEvents(
    [
      {
        id: "evt-pdf-password",
        event_type: "final_detail",
        run_id: "run-pdf-password",
        timestamp: "2026-08-20T01:00:00.000Z",
        data: {
          run_id: "run-pdf-password",
          projection_version: "ai-platform.chat-public-projection.v1",
          detail_kind: "failed",
          detail_code: "context_file_pdf_password_required",
          message: "private parser exception at C:\\runtime\\secret.pdf",
        },
      } satisfies HistoryEvent,
    ],
    new Set<string>(),
    { activeSubagentStack: [] },
  );

  const assistant = messages.find((message) => message.role === "assistant");
  assert.ok(assistant);
  assert.equal(
    assistant.content,
    "PDF 文件需要密码。请先解除密码保护后重新上传。",
  );
  const status = assistant.parts?.find((part) => part.type === "run_status");
  assert.equal(status?.type, "run_status");
  if (status?.type !== "run_status") throw new Error("expected run status");
  assert.equal(status.event_type, "context_file_pdf_password_required");
  assert.doesNotMatch(JSON.stringify(assistant), /private|runtime|secret\.pdf/i);
});

test("historical reconstruction rejects a recognized code from a foreign projection", () => {
  const messages = reconstructMessagesFromEvents(
    [
      {
        id: "evt-foreign-pdf-password",
        event_type: "final_detail",
        run_id: "run-foreign-pdf-password",
        timestamp: "2026-08-20T01:00:00.000Z",
        data: {
          run_id: "run-foreign-pdf-password",
          projection_version: "foreign-projection.v1",
          detail_kind: "failed",
          detail_code: "context_file_pdf_password_required",
          message: "private parser exception at C:\\runtime\\secret.pdf",
        },
      } satisfies HistoryEvent,
    ],
    new Set<string>(),
    { activeSubagentStack: [] },
  );

  const serialized = JSON.stringify(messages);
  assert.doesNotMatch(serialized, /PDF 文件需要密码|private|runtime|secret\.pdf/i);
  assert.equal(
    messages.some((message) =>
      message.parts?.some((part) => part.type === "run_status"),
    ),
    false,
  );
});
