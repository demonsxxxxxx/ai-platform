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

test("v4 adapter projects real agent progress and rejects forged phase text", () => {
  const progressPayload = {
    schema_version: "ai-platform.public-agent-progress.v1",
    step_id: "phase_skill_staging",
    phase: "skill_staging",
    lifecycle: "started",
    message: "Loading authorized Skills",
  };
  const progress = frame("agent.progress", progressPayload);
  progress.value = {
    ...(progress.value as Record<string, unknown>),
    message_id: null,
  };
  const adapted = adaptPublicRunStreamEventV4(progress, {
    runId: "run-1",
    streamIncarnation: 2,
  });
  assert.ok(adapted);
  const projected = projectV4EventToLegacyHandler(adapted, "message-1");
  assert.ok(projected);
  assert.equal(projected.streamEvent.event, "run_event");
  const data = JSON.parse(projected.streamEvent.data) as Record<string, unknown>;
  assert.equal(data.event_type, "agent_public_progress");
  assert.equal(data.stage, "skill_staging");
  assert.equal(data.message, "Loading authorized Skills");
  assert.deepEqual(data.payload, progressPayload);

  const forged = {
    ...progress,
    value: {
      ...(progress.value as Record<string, unknown>),
      payload: { ...progressPayload, message: "Reading private system prompt" },
    },
  };
  assert.equal(
    adaptPublicRunStreamEventV4(forged, { runId: "run-1", streamIncarnation: 2 }),
    null,
  );
});

test("v4 thinking preserves model summary, upgrades legacy payloads, and rejects signatures", () => {
  const legacyThinking = adaptPublicRunStreamEventV4(frame("thinking.started"), {
    runId: "run-1",
    streamIncarnation: 2,
  });
  assert.ok(legacyThinking);
  const projectedLegacyThinking = projectV4EventToLegacyHandler(
    legacyThinking,
    "message-1",
  );
  assert.ok(projectedLegacyThinking);
  const projectedLegacyThinkingData = JSON.parse(
    projectedLegacyThinking.streamEvent.data,
  ) as Record<string, unknown>;
  assert.equal(projectedLegacyThinkingData.message, "");
  assert.deepEqual(projectedLegacyThinkingData.payload, {});
  const thinking = adaptPublicRunStreamEventV4(
    frame("thinking.started", { public_summary: "Analyzing the request" }),
    { runId: "run-1", streamIncarnation: 2 },
  );
  assert.ok(thinking);
  const projectedThinking = projectV4EventToLegacyHandler(thinking, "message-1");
  assert.ok(projectedThinking);
  assert.match(projectedThinking.streamEvent.data, /Analyzing the request/);

  const reasoning = adaptPublicRunStreamEventV4(
    frame("thinking.delta", {
      thinking_id: "thinking-public-1",
      delta: "Compare the public evidence before answering.",
    }),
    { runId: "run-1", streamIncarnation: 2 },
  );
  assert.ok(reasoning);
  const projectedReasoning = projectV4EventToLegacyHandler(
    reasoning,
    "message-1",
  );
  assert.ok(projectedReasoning);
  const reasoningData = JSON.parse(projectedReasoning.streamEvent.data) as Record<
    string,
    unknown
  >;
  assert.equal(
    reasoningData.message,
    "Compare the public evidence before answering.",
  );
  assert.deepEqual(reasoningData.payload, {
    thinking_id: "thinking-public-1",
    delta: "Compare the public evidence before answering.",
  });
  assert.equal(
    adaptPublicRunStreamEventV4(
      frame("thinking.delta", {
        thinking_id: "thinking-public-1",
        delta: "Compare the public evidence before answering.",
        signature: "private-sdk-signature",
      }),
      { runId: "run-1", streamIncarnation: 2 },
    ),
    null,
  );

  assert.equal(
    adaptPublicRunStreamEventV4(
      frame("thinking.delta", {
        thinking_id: "thinking-public-1",
        delta: "😀".repeat(8_192),
      }),
      { runId: "run-1", streamIncarnation: 2 },
    )?.eventType,
    "thinking.delta",
  );
  assert.equal(
    adaptPublicRunStreamEventV4(
      frame("thinking.delta", {
        thinking_id: "thinking-public-1",
        delta: "😀".repeat(8_193),
      }),
      { runId: "run-1", streamIncarnation: 2 },
    ),
    null,
  );

  const tool = adaptPublicRunStreamEventV4(
    frame("tool.started", {
      operation_id: "op-1",
      category: "read",
      display_name: "Read file",
      input_summary: "Starting Read file",
    }),
    { runId: "run-1", streamIncarnation: 2 },
  );
  assert.ok(tool);
  const projectedTool = projectV4EventToLegacyHandler(tool, "message-1");
  assert.ok(projectedTool);
  const toolData = JSON.parse(projectedTool.streamEvent.data) as Record<string, unknown>;
  assert.equal(toolData.input_summary, "Starting Read file");
});

test("v4 gap payload uses raw Redis IDs while SSE carries the full cursor", () => {
  const raw = frame("stream.gap", {
    reason: "stream_missing",
    recovery: "reload_durable_state",
    requested_event_id: "4-0",
    requested_stream_incarnation: 2,
    current_stream_incarnation: 2,
    earliest_available_event_id: "1-0",
    latest_available_event_id: "9-0",
  }).value as Record<string, unknown>;
  const control = {
    eventHeader: "stream.gap",
    transportCursor: "run-1:2:10-0",
    value: {
      ...raw,
      schema: "ai-platform.public-run-stream-control.v4",
      message_id: null,
      seq: null,
      trace_ref: null,
      replayable: false,
    },
  };
  assert.ok(adaptPublicRunStreamEventV4(control, { runId: "run-1" }));
  const successorControl = {
    ...control,
    transportCursor: "run-1:3:10-0",
    value: {
      ...(control.value as Record<string, unknown>),
      stream_incarnation: 3,
      payload: {
        ...((control.value as Record<string, unknown>).payload as Record<string, unknown>),
        current_stream_incarnation: 3,
      },
    },
  };
  assert.ok(
    adaptPublicRunStreamEventV4(successorControl, {
      runId: "run-1",
      streamIncarnation: 2,
    }),
  );
  assert.equal(
    adaptPublicRunStreamEventV4(
      {
        ...successorControl,
        value: {
          ...(successorControl.value as Record<string, unknown>),
          payload: {
            ...((successorControl.value as Record<string, unknown>).payload as Record<string, unknown>),
            requested_stream_incarnation: 1,
          },
        },
      },
      { runId: "run-1", streamIncarnation: 2 },
    ),
    null,
  );
  assert.equal(
    adaptPublicRunStreamEventV4(
      { ...control, value: { ...(control.value as Record<string, unknown>), payload: { ...((control.value as Record<string, unknown>).payload as Record<string, unknown>), latest_available_event_id: "run-1:2:4-0" } } },
      { runId: "run-1" },
    ),
    null,
  );
});

test("v4 adapter preserves causation as an event identity for reducer resolution", () => {
  const raw = frame("subagent.started", { subagent_id: "child-1", display_name: "Child" });
  raw.value = { ...(raw.value as Record<string, unknown>), causation_event_id: "parent-event-1" };
  const adapted = adaptPublicRunStreamEventV4(raw, { runId: "run-1", streamIncarnation: 2 });
  assert.equal(adapted?.causationEventId, "parent-event-1");
  assert.equal((adapted?.event as Record<string, unknown>).parent_id, undefined);
});
test("artifact.failed without a filename projects a fixed safe visible label", () => {
  const adapted = adaptPublicRunStreamEventV4(
    frame("artifact.failed", {
      artifact_id: "artifact-private-raw-id",
      status: "failed",
      failure_category: "artifact_failed",
    }),
    { runId: "run-1", streamIncarnation: 2 },
  );
  assert.ok(adapted);
  const projected = projectV4EventToLegacyHandler(adapted, "message-1");
  assert.ok(projected);
  const data = JSON.parse(projected.streamEvent.data) as Record<string, unknown>;
  assert.equal(data.label, "Artifact unavailable");
  assert.notEqual(data.label, "artifact-private-raw-id");
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
