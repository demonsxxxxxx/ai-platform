import test from "node:test";
import assert from "node:assert/strict";
import { adaptPublicRunStreamEventV4 } from "../publicEventAdapter";
import {
  createPublicV4ReducerState,
  reducePublicV4Event,
} from "../publicEventReducer";

function event(eventType: string, payload: Record<string, unknown>, seq: number, id = `event-${seq}`) {
  const adapted = adaptPublicRunStreamEventV4({
    eventHeader: eventType,
    transportCursor: `cursor-${id}`,
    value: {
      schema: "ai-platform.public-run-stream-event.v4",
      event_id: id,
      run_id: "run-1",
      message_id: "message-1",
      seq,
      event_type: eventType,
      stream_incarnation: 1,
      replayable: true,
      trace_ref: null,
      causation_event_id: null,
      emitted_at: "2026-01-01T00:00:00Z",
      payload,
    },
  }, { runId: "run-1", streamIncarnation: 1 });
  assert.ok(adapted);
  return adapted;
}

test("reducer accepts private sequence gaps and advances cursor on semantic no-op", () => {
  let state = createPublicV4ReducerState([], { sessionId: "session-1", runId: "run-1", generation: 1, streamIncarnation: 1 });
  let reduction = reducePublicV4Event(state, event("message.started", {}, 1));
  state = reduction.state;
  reduction = reducePublicV4Event(state, event("message.delta", { delta: "A" }, 4));
  state = reduction.state;
  assert.equal(reduction.semanticApplied, true);
  reduction = reducePublicV4Event(state, event("message.delta", { delta: "A" }, 4, "event-duplicate"));
  assert.equal(reduction.accepted, true);
  assert.equal(reduction.semanticApplied, false);
  assert.equal(reduction.state.acceptedTransportCursor, "cursor-event-duplicate");
  assert.equal(reduction.state.messages[0]?.content, "A");
});

test("final content replaces streamed content and terminal state does not duplicate the message", () => {
  let state = createPublicV4ReducerState([], { sessionId: "session-1", runId: "run-1", generation: 1, streamIncarnation: 1 });
  state = reducePublicV4Event(state, event("message.delta", { delta: "partial" }, 2)).state;
  state = reducePublicV4Event(state, event("message.completed", { content: "final" }, 3)).state;
  state = reducePublicV4Event(state, event("run.succeeded", { terminal_event_id: "terminal-1", hydrate_required: true }, 4)).state;
  assert.equal(state.messages.length, 1);
  assert.equal(state.messages[0]?.content, "final");
  assert.equal(state.messages[0]?.isStreaming, false);
  assert.equal(state.terminal, true);
});

test("stale sequence, foreign incarnation, and stale generation are rejected", () => {
  let state = createPublicV4ReducerState([], { sessionId: "session-1", runId: "run-1", generation: 2, streamIncarnation: 1 });
  state = reducePublicV4Event(state, event("message.delta", { delta: "A" }, 3)).state;
  assert.equal(reducePublicV4Event(state, event("message.delta", { delta: "B" }, 2)).accepted, false);
  const foreign = { ...event("message.delta", { delta: "C" }, 4), streamIncarnation: 2 };
  assert.equal(reducePublicV4Event(state, foreign).accepted, false);
  assert.equal(state.binding.generation, 2);
});
