import test from "node:test";
import assert from "node:assert/strict";
import {
  adaptPublicRunStreamEventV4,
  projectV4EventToLegacyHandler,
} from "../publicEventAdapter";

function frame(eventType: string, payload: Record<string, unknown> = {}, seq = 1): {
  eventHeader: string;
  transportCursor: string;
  value: Record<string, unknown>;
} {
  return {
    eventHeader: eventType,
    transportCursor: `run-1:2:${seq}-0`,
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
  assert.equal(adapted?.transportCursor, "run-1:2:1-0");
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

test("v4 adapter keeps semantic identity separate from the Redis transport cursor", () => {
  const first = adaptPublicRunStreamEventV4(frame("message.delta", { delta: "a" }, 1), { runId: "run-1" });
  const replay = adaptPublicRunStreamEventV4({
    ...frame("message.delta", { delta: "a" }, 2),
    value: { ...(frame("message.delta", { delta: "a" }, 2).value as Record<string, unknown>), event_id: "event-1" },
  }, { runId: "run-1" });
  assert.ok(first);
  assert.ok(replay);
  assert.equal(first.semanticKey, "event-1");
  assert.equal(replay.semanticKey, "event-1");
  assert.equal(first.transportCursor, "run-1:2:1-0");
  assert.equal(replay.transportCursor, "run-1:2:2-0");
});

test("v4 adapter enforces generated run, date-time, nullable, and exact payload rules", () => {
  const valid = frame("run.failed", {
    terminal_event_id: "terminal-1",
    hydrate_required: true,
    projection_version: "ai-platform.chat-public-projection.v1",
    code: "failed",
    default_message: "Run failed",
    detail: null,
  });
  assert.ok(adaptPublicRunStreamEventV4(valid, { runId: "run-1" }));
  const invalidRun = { ...valid, value: { ...(valid.value as Record<string, unknown>), run_id: `x${"a".repeat(128)}` } };
  assert.equal(adaptPublicRunStreamEventV4(invalidRun, { runId: `x${"a".repeat(128)}` }), null);
  const invalidDate = { ...valid, value: { ...(valid.value as Record<string, unknown>), emitted_at: "2026-02-30T00:00:00Z" } };
  assert.equal(adaptPublicRunStreamEventV4(invalidDate, { runId: "run-1" }), null);
  const invalidExtra = { ...valid, value: { ...(valid.value as Record<string, unknown>), payload: { ...(valid.value as Record<string, unknown>).payload as Record<string, unknown>, secret: true } } };
  assert.equal(adaptPublicRunStreamEventV4(invalidExtra, { runId: "run-1" }), null);
});

test("v4 adapter requires a stream-incarnation Redis transport cursor", () => {
  const value = frame("message.delta", { delta: "hi" });
  assert.equal(
    adaptPublicRunStreamEventV4(
      { ...value, transportCursor: "cursor-1" },
      { runId: "run-1", streamIncarnation: 2 },
    ),
    null,
  );
  assert.ok(
    adaptPublicRunStreamEventV4(value, {
      runId: "run-1",
      streamIncarnation: 2,
    }),
  );
});

test("v4 adapter preserves nullable run-level identity and projects safe activity", () => {
  const value = frame("run.cancel_requested", { source: "user" });
  value.value = {
    ...(value.value as Record<string, unknown>),
    message_id: null,
    seq: 2,
  };
  const adapted = adaptPublicRunStreamEventV4(value, { runId: "run-1" });
  assert.ok(adapted);
  assert.equal(adapted.messageId, null);
  const projected = projectV4EventToLegacyHandler(adapted, "message-1");
  assert.ok(projected);
  assert.equal(projected.streamEvent.event, "run_event");
  assert.match(projected.streamEvent.data, /cancel_requested/);
});

test("stream.end remains transport-only and preserves its terminal receipt", () => {
  const raw = frame("stream.end", { terminal_event_id: "terminal-1" }).value as Record<string, unknown>;
  const adapted = adaptPublicRunStreamEventV4({
    eventHeader: "stream.end",
    transportCursor: "run-1:2:1-0",
    value: {
      ...raw,
      schema: "ai-platform.public-run-stream-control.v4",
      message_id: null,
      seq: null,
      trace_ref: null,
    },
  }, { runId: "run-1" });
  assert.ok(adapted);
  const projected = projectV4EventToLegacyHandler(adapted, "message-1");
  assert.ok(projected);
  assert.equal(projected.streamEvent.event, "end");
  const data = JSON.parse(projected.streamEvent.data) as Record<string, unknown>;
  assert.deepEqual(data.payload, { terminal_event_id: "terminal-1" });
  assert.equal(data.status, undefined);
});
