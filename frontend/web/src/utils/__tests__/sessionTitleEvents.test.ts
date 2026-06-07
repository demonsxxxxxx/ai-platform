import test from "node:test";
import assert from "node:assert/strict";
import {
  dispatchSessionTitleUpdated,
  getCachedSessionTitle,
  listenSessionTitleUpdated,
} from "../sessionTitleEvents.ts";

test("dispatches generated session title updates to listeners", () => {
  const target = new EventTarget();
  const received: Array<{ sessionId: string; title: string }> = [];

  const cleanup = listenSessionTitleUpdated((detail) => {
    received.push(detail);
  }, target);

  dispatchSessionTitleUpdated(
    { sessionId: "session-1", title: "Generated title" },
    target,
  );

  assert.deepEqual(received, [
    { sessionId: "session-1", title: "Generated title" },
  ]);
  assert.equal(getCachedSessionTitle("session-1"), "Generated title");

  cleanup();
  dispatchSessionTitleUpdated(
    { sessionId: "session-1", title: "Ignored title" },
    target,
  );

  assert.deepEqual(received, [
    { sessionId: "session-1", title: "Generated title" },
  ]);
});
