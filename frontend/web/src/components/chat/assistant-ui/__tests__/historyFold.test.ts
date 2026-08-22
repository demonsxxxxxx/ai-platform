import test from "node:test";
import assert from "node:assert/strict";
import { adaptPublicRunStreamEventV4 } from "../publicEventAdapter";
import { foldPublicV4History } from "../historyFold";

function event(seq: number, delta: string, id = `event-${seq}`) {
  const adapted = adaptPublicRunStreamEventV4({
    eventHeader: "message.delta",
    transportCursor: `cursor-${id}`,
    value: {
      schema: "ai-platform.public-run-stream-event.v4",
      event_id: id,
      run_id: "run-1",
      message_id: "message-1",
      seq,
      event_type: "message.delta",
      stream_incarnation: 4,
      replayable: true,
      trace_ref: null,
      causation_event_id: null,
      emitted_at: "2026-01-01T00:00:00Z",
      payload: { delta },
    },
  }, { runId: "run-1", streamIncarnation: 4 });
  assert.ok(adapted);
  return adapted;
}

test("history fold and live reducer converge for ordered replay with duplicate frames", () => {
  const binding = { sessionId: "session-1", runId: "run-1", generation: 7, streamIncarnation: 4 };
  const result = foldPublicV4History([event(3, "C"), event(1, "A"), event(2, "B"), event(2, "B", "replay-copy")], binding);
  assert.equal(result.acceptedEvents, 4);
  assert.equal(result.state.messages[0]?.content, "ABC");
  assert.equal(result.state.acceptedSequence, 3);
});

test("history fold rejects stale identity without mutating the seed", () => {
  const binding = { sessionId: "session-1", runId: "run-1", generation: 7, streamIncarnation: 4 };
  const result = foldPublicV4History([event(1, "A")], { ...binding, streamIncarnation: 5 });
  assert.equal(result.acceptedEvents, 0);
  assert.equal(result.rejectedEvents, 1);
  assert.deepEqual(result.state.messages, []);
});
