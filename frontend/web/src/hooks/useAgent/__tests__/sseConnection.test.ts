import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { getSSECloseAction, isTerminalSSEEvent } from "../sseConnection.ts";

const __dirname = dirname(fileURLToPath(import.meta.url));

test("SSE uses the same explicit cookie-session credential boundary", () => {
  const source = readFileSync(resolve(__dirname, "../sseConnection.ts"), "utf8");
  assert.match(source, /credentials:\s*"include"/);
});

test("retries an SSE close that arrives before a terminal stream event", () => {
  assert.equal(
    getSSECloseAction({
      receivedTerminalEvent: false,
    }),
    "retry",
  );
});

test("treats SSE close as terminal only after done or task error", () => {
  assert.equal(isTerminalSSEEvent("message:chunk"), false);
  assert.equal(isTerminalSSEEvent("done"), true);
  assert.equal(isTerminalSSEEvent("complete"), true);
  assert.equal(isTerminalSSEEvent("user:cancel"), false);
  assert.equal(isTerminalSSEEvent("error", { type: "ValueError" }), true);

  assert.equal(
    getSSECloseAction({
      receivedTerminalEvent: true,
    }),
    "terminal",
  );
});

test("does not treat transport-level SSE errors as terminal task events", () => {
  assert.equal(
    isTerminalSSEEvent("error", { error: "An internal error occurred" }),
    false,
  );
});
