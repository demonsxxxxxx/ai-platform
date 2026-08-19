import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

function getStopGenerationSource(): string {
  const source = readFileSync(resolve(__dirname, "../../useAgent.ts"), "utf8");
  const stopGenerationStart = source.indexOf("const stopGeneration = useCallback");
  const clearMessagesStart = source.indexOf("const clearMessages = useCallback", stopGenerationStart);

  assert.notEqual(stopGenerationStart, -1);
  assert.notEqual(clearMessagesStart, -1);

  return source.slice(stopGenerationStart, clearMessagesStart);
}

test("stopGeneration delegates the real run id to the run-control lifecycle", () => {
  const source = getStopGenerationSource();

  assert.match(source, /const currentRunId = currentRunIdRef\.current/);
  assert.match(source, /return runControlLifecycle\.cancel\(\)/);
  assert.doesNotMatch(source, /sessionApi\.cancel\(currentSessionId\)/);
  assert.doesNotMatch(source, /\/chat\/sessions\/\$\{sessionId\}\/cancel/);
});

test("stopGeneration returns unavailable instead of claiming cancellation without a trusted run", () => {
  const source = getStopGenerationSource();

  assert.match(source, /if \(!currentRunId \|\| !currentSessionId\) \{\s*return "unavailable" as const;/);
  assert.doesNotMatch(source, /toast\.custom/);
});

test("the stop confirmation awaits the command result before showing acknowledgement", () => {
  const source = readFileSync(
    resolve(__dirname, "../../../components/chat/ChatInput.tsx"),
    "utf8",
  );
  const confirmStart = source.indexOf('<ConfirmDialog\n        isOpen={stopConfirmOpen}');
  const contactDialogStart = source.indexOf("<ContactAdminDialog", confirmStart);
  assert.notEqual(confirmStart, -1);
  assert.notEqual(contactDialogStart, -1);

  const confirm = source.slice(confirmStart, contactDialogStart);
  const awaitStop = confirm.indexOf("result = await onStop()");
  const closeDialog = confirm.indexOf("setStopConfirmOpen(false)", awaitStop);
  const acknowledged = confirm.indexOf('result !== "acknowledged"');
  const requestedCopy = confirm.indexOf('t("chat.runStatus.event.cancelRequested")');

  assert.ok(awaitStop >= 0 && acknowledged > awaitStop);
  assert.ok(closeDialog > acknowledged && requestedCopy > closeDialog);
  assert.match(confirm, /loading=\{isStopSubmitting\}/);
  assert.doesNotMatch(confirm, /chat\.status\.cancelled/);
});
