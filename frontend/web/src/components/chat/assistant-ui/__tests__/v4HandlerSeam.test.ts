import test from "node:test";
import assert from "node:assert/strict";
import {
  handlePublicRunStreamFrameV4,
  type EventHandlerContext,
} from "../../../../hooks/useAgent/eventHandlers";
import type { StreamEventBinding } from "../../../../hooks/useAgent/eventHandlers";
import {
  adaptPublicRunStreamEventV4,
  projectV4EventToLegacyHandler,
} from "../publicEventAdapter";

function frame(eventType: string, payload: Record<string, unknown>, messageId: string | null = null) {
  return adaptPublicRunStreamEventV4(
    {
      eventHeader: eventType,
      transportCursor: "run-1:1:1-0",
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
  const failed = frame("run.failed", {
    terminal_event_id: "terminal-2",
    hydrate_required: true,
    projection_version: "ai-platform.chat-public-projection.v1",
    code: "run_failed",
    default_message: "Run failed",
    detail: null,
  });
  assert.ok(failed);
  const projectedFailed = projectV4EventToLegacyHandler(failed, "message-1");
  assert.ok(projectedFailed);
  assert.equal(projectedFailed.streamEvent.event, "final_detail");
  assert.match(projectedFailed.streamEvent.data, /detail_kind.*failed/);
  assert.doesNotMatch(projectedFailed.streamEvent.data, /Run failed/);
});

test("v4 handler is executable assembly through the existing event owner", () => {
  const ctx = {
    sessionIdRef: { current: "session-1" },
    currentRunIdRef: { current: "run-1" },
    processedEventIdsRef: { current: new Set<string>() },
    acceptedStreamCursorRef: { current: { sessionId: null, runId: null, eventId: null } },
    lastHistoryTimestampRef: { current: null },
    activeSubagentStackRef: { current: [] },
    streamVersionRef: { current: 0 },
    setSessionId: () => undefined,
    setMessages: () => undefined,
    setConnectionStatus: () => undefined,
    setIsInitializingSandbox: () => undefined,
    setSandboxError: () => undefined,
  } as unknown as EventHandlerContext;
  const binding: StreamEventBinding = { sessionId: "session-1", runId: "run-1", streamVersion: 0 };
  let committed = 0;
  const accepted = handlePublicRunStreamFrameV4({
    frame: {
      eventHeader: "stream.open",
      transportCursor: "run-1:1:1700000000000-0",
      value: {
        schema: "ai-platform.public-run-stream-control.v4",
        event_id: "event-open",
        run_id: "run-1",
        message_id: null,
        seq: null,
        event_type: "stream.open",
        stream_incarnation: 1,
        replayable: true,
        trace_ref: null,
        causation_event_id: null,
        emitted_at: "2026-01-01T00:00:00Z",
        payload: { design_id: "ai-platform.redis-streams-sse-event-channel.v4" },
      },
    },
    adapterBinding: { runId: "run-1", streamIncarnation: 1 },
    messageId: "message-1",
    ctx,
    binding,
    onCommitted: (semanticApplied) => { if (semanticApplied) committed += 1; },
  });
  assert.equal(accepted, true);
  assert.equal(committed, 1);
});

test("v4 handler advances transport cursor for semantic duplicates without reapplying them", () => {
  const ctx = {
    sessionIdRef: { current: "session-1" },
    currentRunIdRef: { current: "run-1" },
    processedEventIdsRef: { current: new Set<string>() },
    acceptedStreamCursorRef: { current: { sessionId: "session-1", runId: "run-1", eventId: null } },
    lastHistoryTimestampRef: { current: null },
    activeSubagentStackRef: { current: [] },
    streamVersionRef: { current: 0 },
    setSessionId: () => undefined,
    setMessages: () => undefined,
    setConnectionStatus: () => undefined,
    setIsInitializingSandbox: () => undefined,
    setSandboxError: () => undefined,
  } as unknown as EventHandlerContext;
  const binding: StreamEventBinding = { sessionId: "session-1", runId: "run-1", streamVersion: 0 };
  const first = {
    eventHeader: "stream.heartbeat",
    transportCursor: "run-1:1:1-0",
    value: {
      schema: "ai-platform.public-run-stream-control.v4",
      event_id: "heartbeat-1",
      run_id: "run-1",
      message_id: null,
      seq: null,
      event_type: "stream.heartbeat",
      stream_incarnation: 1,
      replayable: false,
      trace_ref: null,
      causation_event_id: null,
      emitted_at: "2026-01-01T00:00:00Z",
      payload: { status: "running" },
    },
  };
  const second = { ...first, transportCursor: "run-1:1:2-0" };
  let semanticCommits = 0;
  let transportOnlyCommits = 0;
  const onCommitted = (semanticApplied: boolean) => {
    if (semanticApplied) semanticCommits += 1;
    else transportOnlyCommits += 1;
    ctx.acceptedStreamCursorRef!.current.eventId = semanticApplied
      ? (semanticCommits === 1 ? "run-1:1:1-0" : ctx.acceptedStreamCursorRef!.current.eventId)
      : "run-1:1:2-0";
  };
  assert.equal(handlePublicRunStreamFrameV4({ frame: first, adapterBinding: { runId: "run-1", streamIncarnation: 1 }, messageId: "message-1", ctx, binding, onCommitted }), true);
  assert.equal(handlePublicRunStreamFrameV4({ frame: second, adapterBinding: { runId: "run-1", streamIncarnation: 1 }, messageId: "message-1", ctx, binding, onCommitted }), false);
  assert.equal(semanticCommits, 1);
  assert.equal(transportOnlyCommits, 1);
  assert.equal(ctx.acceptedStreamCursorRef!.current.eventId, "run-1:1:2-0");
});
test("v4 terminal binding is checked before hydration side effects", () => {
  const ctx = {
    sessionIdRef: { current: "session-1" },
    currentRunIdRef: { current: "run-1" },
    processedEventIdsRef: { current: new Set<string>() },
    lastHistoryTimestampRef: { current: null },
    activeSubagentStackRef: { current: [] },
    streamVersionRef: { current: 8 },
    v4TerminalFenceRef: { current: null },
    v4TerminalEventIdsRef: { current: new Set<string>() },
    setSessionId: () => undefined,
    setMessages: () => undefined,
    setConnectionStatus: () => undefined,
    setIsInitializingSandbox: () => undefined,
    setSandboxError: () => undefined,
    onRunTerminal: () => {
      throw new Error("stale terminal hydrated");
    },
  } as unknown as EventHandlerContext;
  const terminal = frame("run.succeeded", {
    terminal_event_id: "terminal-stale",
    hydrate_required: true,
  }, "message-1");
  assert.ok(terminal);
  assert.equal(handlePublicRunStreamFrameV4({
    frame: {
      eventHeader: "run.succeeded",
      transportCursor: "run-1:1:1-0",
      generation: 4,
      value: terminal.event,
    },
    adapterBinding: { runId: "run-1", streamIncarnation: 1, generation: 4 },
    messageId: "message-1",
    ctx,
    binding: { sessionId: "session-1", runId: "run-1", streamVersion: 7 },
  }), false);
});

test("v4 terminal rejects a foreign Run before hydration", () => {
  const ctx = {
    sessionIdRef: { current: "session-1" },
    currentRunIdRef: { current: "run-1" },
    processedEventIdsRef: { current: new Set<string>() },
    lastHistoryTimestampRef: { current: null },
    activeSubagentStackRef: { current: [] },
    streamVersionRef: { current: 8 },
    v4TerminalFenceRef: { current: null },
    v4TerminalEventIdsRef: { current: new Set<string>() },
    setSessionId: () => undefined,
    setMessages: () => undefined,
    setConnectionStatus: () => undefined,
    setIsInitializingSandbox: () => undefined,
    setSandboxError: () => undefined,
    onRunTerminal: () => {
      throw new Error("foreign terminal hydrated");
    },
  } as unknown as EventHandlerContext;
  const foreign = adaptPublicRunStreamEventV4(
    {
      eventHeader: "run.succeeded",
      transportCursor: "run-2:1:1-0",
      value: {
        schema: "ai-platform.public-run-stream-event.v4",
        event_id: "foreign-terminal",
        run_id: "run-2",
        message_id: "message-2",
        seq: 1,
        event_type: "run.succeeded",
        stream_incarnation: 1,
        replayable: true,
        trace_ref: null,
        causation_event_id: null,
        emitted_at: "2026-01-01T00:00:00Z",
        payload: { terminal_event_id: "foreign-terminal", hydrate_required: true },
      },
    },
    { runId: "run-2", streamIncarnation: 1 },
  );
  assert.ok(foreign);
  assert.equal(handlePublicRunStreamFrameV4({
    frame: {
      eventHeader: "run.succeeded",
      transportCursor: "run-2:1:1-0",
      value: foreign.event,
    },
    adapterBinding: { runId: "run-2", streamIncarnation: 1 },
    messageId: "message-2",
    ctx,
    binding: { sessionId: "session-1", runId: "run-1", streamVersion: 8 },
  }), false);
});

test("v4 terminal end waits for authoritative hydration and scopes the fence", () => {
  const ctx = {
    sessionIdRef: { current: "session-1" },
    currentRunIdRef: { current: "run-1" },
    processedEventIdsRef: { current: new Set<string>() },
    acceptedStreamCursorRef: { current: { sessionId: "session-1", runId: "run-1", eventId: null, streamIncarnation: null } },
    lastHistoryTimestampRef: { current: null },
    activeSubagentStackRef: { current: [] },
    streamVersionRef: { current: 7 },
    v4TerminalFenceRef: { current: null },
    setSessionId: () => undefined,
    setMessages: () => undefined,
    setConnectionStatus: () => undefined,
    setIsInitializingSandbox: () => undefined,
    setSandboxError: () => undefined,
    onRunTerminal: (_runId: string, _status: "succeeded" | "failed" | "cancelled", _messageId: string, onAccepted?: () => void) => {
      hydrationAccepted = () => {
        ctx.currentRunIdRef.current = null;
        ctx.streamVersionRef.current = 8;
        ctx.v4TerminalFenceRef!.current = null;
        onAccepted?.();
      };
      return true;
    },
  } as unknown as EventHandlerContext;
  const binding: StreamEventBinding = { sessionId: "session-1", runId: "run-1", streamVersion: 7 };
  let hydrationAccepted: (() => void) | undefined;
  const terminal = {
    eventHeader: "run.succeeded",
    transportCursor: "run-1:3:1-0",
    generation: 2,
    value: {
      schema: "ai-platform.public-run-stream-event.v4",
      event_id: "terminal-event",
      run_id: "run-1",
      message_id: "message-1",
      seq: 1,
      event_type: "run.succeeded",
      stream_incarnation: 3,
      replayable: true,
      trace_ref: null,
      causation_event_id: null,
      emitted_at: "2026-01-01T00:00:00Z",
      payload: { terminal_event_id: "terminal-event", hydrate_required: true },
    },
  };
  const end = {
    eventHeader: "stream.end",
    transportCursor: "run-1:3:2-0",
    generation: 2,
    value: {
      schema: "ai-platform.public-run-stream-control.v4",
      event_id: "end-event",
      run_id: "run-1",
      message_id: null,
      seq: null,
      event_type: "stream.end",
      stream_incarnation: 3,
      replayable: true,
      trace_ref: null,
      causation_event_id: null,
      emitted_at: "2026-01-01T00:00:01Z",
      payload: { terminal_event_id: "terminal-event" },
    },
  };
  assert.equal(handlePublicRunStreamFrameV4({ frame: terminal, adapterBinding: { runId: "run-1", streamIncarnation: 3, generation: 2 }, messageId: "message-1", ctx, binding }), true);
  assert.equal(handlePublicRunStreamFrameV4({ frame: end, adapterBinding: { runId: "run-1", streamIncarnation: 3, generation: 2 }, messageId: "message-1", ctx, binding }), false);
  assert.ok(hydrationAccepted);
  hydrationAccepted();
  assert.equal(ctx.v4TerminalFenceRef?.current?.sessionId, "session-1");
  assert.equal(ctx.v4TerminalFenceRef?.current?.streamIncarnation, 3);
  assert.equal(ctx.v4TerminalFenceRef?.current?.generation, 2);
  assert.equal(handlePublicRunStreamFrameV4({ frame: end, adapterBinding: { runId: "run-1", streamIncarnation: 3, generation: 2 }, messageId: "message-1", ctx, binding }), true);
});

test("v4 terminal receipt survives real finalization for every terminal outcome", () => {
  const cases = [
    ["run.succeeded", { terminal_event_id: "terminal-succeeded", hydrate_required: true }, "succeeded"],
    ["run.cancelled", { terminal_event_id: "terminal-cancelled", hydrate_required: true, reason_code: "user_cancelled" }, "cancelled"],
    ["run.failed", { terminal_event_id: "terminal-failed", hydrate_required: true, projection_version: "ai-platform.chat-public-projection.v1", code: "run_failed", default_message: "Run failed", detail: null }, "failed"],
  ] as const;
  for (const [eventType, payload, status] of cases) {
    const ctx = {
      sessionIdRef: { current: "session-1" },
      currentRunIdRef: { current: "run-1" },
      processedEventIdsRef: { current: new Set<string>() },
      acceptedStreamCursorRef: { current: { sessionId: "session-1", runId: "run-1", eventId: null } },
      lastHistoryTimestampRef: { current: null },
      activeSubagentStackRef: { current: [] },
      streamVersionRef: { current: 4 },
      v4TerminalFenceRef: { current: null },
      v4TerminalEventIdsRef: { current: new Set<string>() },
      setSessionId: () => undefined,
      setMessages: () => undefined,
      setConnectionStatus: () => undefined,
      setIsInitializingSandbox: () => undefined,
      setSandboxError: () => undefined,
      onRunTerminal: (_runId: string, observedStatus: string, _messageId: string, onAccepted?: () => void) => {
        assert.equal(observedStatus, status);
        ctx.currentRunIdRef.current = null;
        ctx.streamVersionRef.current = 5;
        onAccepted?.();
        return true;
      },
    } as unknown as EventHandlerContext;
    const terminal = frame(eventType, payload, "message-1");
    assert.ok(terminal);
    const end = {
      eventHeader: "stream.end",
      transportCursor: "run-1:1:2-0",
      generation: 3,
      value: {
        schema: "ai-platform.public-run-stream-control.v4",
        event_id: `end-${status}`,
        run_id: "run-1",
        message_id: null,
        seq: null,
        event_type: "stream.end",
        stream_incarnation: 1,
        replayable: true,
        trace_ref: null,
        causation_event_id: null,
        emitted_at: "2026-01-01T00:00:01Z",
        payload: { terminal_event_id: payload.terminal_event_id },
      },
    };
    const binding = { sessionId: "session-1", runId: "run-1", streamVersion: 4 };
    assert.equal(handlePublicRunStreamFrameV4({
      frame: {
        eventHeader: eventType,
        transportCursor: "run-1:1:1-0",
        generation: 3,
        value: terminal.event,
      },
      adapterBinding: { runId: "run-1", streamIncarnation: 1, generation: 3 },
      messageId: "message-1",
      ctx,
      binding,
    }), true);
    assert.equal(handlePublicRunStreamFrameV4({
      frame: end,
      adapterBinding: { runId: "run-1", streamIncarnation: 1, generation: 3 },
      messageId: "message-1",
      ctx,
      binding,
    }), true);
    assert.equal(ctx.v4TerminalFenceRef?.current, null);
  }
});

test("v4 handler delegates stream gaps to the existing recovery owner", () => {
  const frameValue = {
    schema: "ai-platform.public-run-stream-control.v4",
    event_id: "event-gap",
    run_id: "run-1",
    message_id: null,
    seq: null,
    event_type: "stream.gap",
    stream_incarnation: 1,
    replayable: false,
    trace_ref: null,
    causation_event_id: null,
    emitted_at: "2026-01-01T00:00:00Z",
    payload: {
      reason: "stream_missing",
      requested_event_id: "run-1:1:0-1",
      requested_stream_incarnation: 1,
      earliest_available_event_id: "run-1:1:0-2",
      latest_available_event_id: "run-1:1:0-3",
      current_stream_incarnation: 1,
      recovery: "reload_durable_state",
    },
  };
  let gapEventId = "";
  const accepted = handlePublicRunStreamFrameV4({
    frame: { eventHeader: "stream.gap", transportCursor: "run-1:1:4-0", value: frameValue },
    adapterBinding: { runId: "run-1", streamIncarnation: 1 },
    messageId: "message-1",
    ctx: {} as EventHandlerContext,
    onGap: (event) => { gapEventId = event.eventId; },
  });
  assert.equal(accepted, false);
  assert.equal(gapEventId, "event-gap");
});
