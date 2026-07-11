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

test("stopGeneration cancels the current real run id instead of the session POC endpoint", () => {
  const source = getStopGenerationSource();

  assert.match(source, /const currentRunId = currentRunIdRef\.current/);
  assert.match(source, /await sessionApi\.cancelRun\(currentRunId\)/);
  assert.doesNotMatch(source, /sessionApi\.cancel\(currentSessionId\)/);
  assert.doesNotMatch(source, /\/chat\/sessions\/\$\{sessionId\}\/cancel/);
});

test("stopGeneration fails closed when no trusted current run id exists", () => {
  const source = getStopGenerationSource();
  const currentRunRead = source.indexOf("const currentRunId = currentRunIdRef.current");
  const missingRunGuard = source.indexOf("if (!currentRunId)", currentRunRead);
  const clearLoading = source.indexOf("setIsLoading(false)");
  const dismissQueueToast = source.indexOf('toast.dismiss("chat-queue")');
  const cancelCall = source.indexOf("sessionApi.cancelRun(currentRunId)", currentRunRead);

  assert.notEqual(currentRunRead, -1);
  assert.notEqual(missingRunGuard, -1);
  assert.notEqual(clearLoading, -1);
  assert.notEqual(dismissQueueToast, -1);
  assert.notEqual(cancelCall, -1);
  assert.ok(
    currentRunRead < missingRunGuard &&
      missingRunGuard < clearLoading &&
      clearLoading < dismissQueueToast &&
      dismissQueueToast < cancelCall,
    "stopGeneration must not guess session-to-run when current run id is missing",
  );
});
