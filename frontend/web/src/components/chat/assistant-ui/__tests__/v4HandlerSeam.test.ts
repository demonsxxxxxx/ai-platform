import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import {
  adaptPublicRunStreamEventV4,
  projectV4EventToLegacyHandler,
} from "../publicEventAdapter";

function frame(eventType: string, payload: Record<string, unknown>, messageId: string | null = null) {
  return adaptPublicRunStreamEventV4(
    {
      eventHeader: eventType,
      transportCursor: "cursor-1",
      value: {
        schema: "ai-platform.public-run-stream-event.v4",
        event_id: "event-1",
        run_id: "run-1",
        message_id: messageId,
        seq: 1,
        event_type: eventType,
        stream_incarnation: 1,
        replayable: true,
        trace_ref: null,
        causation_event_id: null,
        emitted_at: "2026-01-01T00:00:00Z",
        payload,
      },
    },
    { runId: "run-1", streamIncarnation: 1 },
  );
}

test("v4 handler seam delegates message and terminal events to legacy owners", () => {
  const delta = frame("message.delta", { delta: "hello" }, "message-1");
  assert.ok(delta);
  const projectedDelta = projectV4EventToLegacyHandler(delta, "message-1");
  assert.ok(projectedDelta);
  assert.equal(projectedDelta.streamEvent.event, "message:chunk");
  assert.match(projectedDelta.streamEvent.data, /chat-public-projection\.v1/);

  const terminal = frame("run.succeeded", { terminal_event_id: "terminal-1", hydrate_required: true });
  assert.ok(terminal);
  const projectedTerminal = projectV4EventToLegacyHandler(terminal, "message-1");
  assert.ok(projectedTerminal);
  assert.equal(projectedTerminal.streamEvent.event, "done");
  assert.match(projectedTerminal.streamEvent.data, /hydrate_required/);
});

test("v4 handler is an additive dispatch seam, not a second reducer owner", () => {
  const root = join(dirname(fileURLToPath(import.meta.url)), "../../../../hooks/useAgent/eventHandlers.ts");
  const source = readFileSync(root, "utf8");
  assert.match(source, /handlePublicRunStreamEventV4/);
  assert.match(source, /return handleStreamEvent\(/);
  assert.doesNotMatch(source, /publicEventReducer|historyFold/);
});
