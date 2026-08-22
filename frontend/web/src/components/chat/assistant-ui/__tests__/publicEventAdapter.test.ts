import test from "node:test";
import assert from "node:assert/strict";
import { adaptPublicRunStreamEventV4 } from "../publicEventAdapter";

function frame(eventType: string, payload: Record<string, unknown> = {}, seq = 1) {
  return {
    eventHeader: eventType,
    transportCursor: `cursor-${seq}`,
    value: {
      schema: "ai-platform.public-run-stream-event.v4",
      event_id: `event-${seq}`,
      run_id: "run-1",
      message_id: "message-1",
      seq,
      event_type: eventType,
      stream_incarnation: 2,
      replayable: true,
      trace_ref: null,
      causation_event_id: null,
      emitted_at: "2026-01-01T00:00:00Z",
      payload,
    },
  };
}

test("v4 adapter accepts generated message delta and retains transport identity", () => {
  const adapted = adaptPublicRunStreamEventV4(frame("message.delta", { delta: "hi" }), {
    runId: "run-1",
    streamIncarnation: 2,
  });
  assert.equal(adapted?.eventType, "message.delta");
  assert.equal(adapted?.messageId, "message-1");
  assert.equal(adapted?.transportCursor, "cursor-1");
});

test("v4 adapter rejects unknown payload fields and foreign run/incarnation", () => {
  assert.equal(
    adaptPublicRunStreamEventV4(frame("message.delta", { delta: "hi", secret: "no" }), { runId: "run-1" }),
    null,
  );
  assert.equal(adaptPublicRunStreamEventV4(frame("message.delta", { delta: "hi" }), { runId: "run-2" }), null);
  assert.equal(
    adaptPublicRunStreamEventV4(frame("message.delta", { delta: "hi" }), { runId: "run-1", streamIncarnation: 3 }),
    null,
  );
});

test("v4 adapter validates control envelope separately", () => {
  const value = frame("stream.open", { design_id: "ai-platform.redis-streams-sse-event-channel.v4" }).value as Record<string, unknown>;
  Object.assign(value, {
    schema: "ai-platform.public-run-stream-control.v4",
    message_id: null,
    seq: null,
    replayable: true,
    trace_ref: null,
  });
  const adapted = adaptPublicRunStreamEventV4({ eventHeader: "stream.open", transportCursor: "0-1", value }, { runId: "run-1" });
  assert.equal(adapted?.eventType, "stream.open");
  assert.equal(adapted?.sequence, null);
});
