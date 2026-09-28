import assert from "node:assert/strict";
import test from "node:test";
import { recoverRunHistory } from "../runHistoryRecovery";
import { recoverReplayGap, type SSEConnectionContext } from "../sseConnection";
import { adaptPublicRunStreamEventV4 } from "../../../components/chat/assistant-ui/publicEventAdapter";
import { ApiRequestError } from "../../../services/api/fetch";

const flush = async () => { for (let i = 0; i < 10; i += 1) await Promise.resolve(); };

test("run history retries a transient failure and returns the actual result", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let calls = 0;
  const result = recoverRunHistory(async () => {
    if (++calls === 1) throw new Error("temporary");
    return { events: ["synthetic final"] };
  }, new AbortController().signal);
  await flush();
  assert.equal(calls, 1);
  t.mock.timers.tick(1000);
  assert.deepEqual(await result, { events: ["synthetic final"] });
  assert.equal(calls, 2);
});

test("run history cancels both stalled requests and retry waits", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  for (const stalled of [false, true]) {
    const owner = new AbortController();
    let calls = 0;
    let requestSignal: AbortSignal | undefined;
    const result = recoverRunHistory(async (signal) => {
      calls += 1;
      requestSignal = signal;
      if (stalled) return new Promise<never>(() => {});
      throw new Error("temporary");
    }, owner.signal);
    const rejected = assert.rejects(result);
    await flush();
    owner.abort();
    await rejected;
    if (stalled) assert.equal(requestSignal?.aborted, true);
    t.mock.timers.tick(60_000);
    await flush();
    assert.equal(calls, 1);
  }
});

test("run history never retries permission failures", async () => {
  let calls = 0;
  await assert.rejects(recoverRunHistory(async () => {
    calls += 1;
    throw Object.assign(new Error("denied"), { status: 403 });
  }, new AbortController().signal));
  assert.equal(calls, 1);
});

test("stalled run-history requests time out and exhaust exactly three attempts", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const signals: AbortSignal[] = [];
  const result = recoverRunHistory((signal) => {
    signals.push(signal);
    return new Promise<never>(() => {});
  }, new AbortController().signal);
  const rejected = assert.rejects(result, /timed out/);
  for (let attempt = 0; attempt < 3; attempt += 1) {
    t.mock.timers.tick(10_000);
    await flush();
    assert.equal(signals[attempt].aborted, true);
    if (attempt < 2) {
      t.mock.timers.tick(1000 * (attempt + 1));
      await flush();
    }
  }
  await rejected;
  assert.equal(signals.length, 3);
});

function gapRecoveryFixture() {
  const states: string[] = [];
  const context = {
    sessionIdRef: { current: "session-a" },
    currentRunIdRef: { current: "run-a" },
    streamVersionRef: { current: 1 },
    streamingMessageIdRef: { current: "assistant-a" },
    replayGapRecoveryRef: { current: null },
    acceptedStreamCursorRef: { current: {
      sessionId: "session-a", runId: "run-a", streamIncarnation: 1, eventId: "run-a:1:1-0",
    } },
    setConnectionStatus: (status: string) => states.push(status),
    setIsInitializingSandbox: () => {},
    hydrateActiveRun: async () => "assistant-a",
  } as unknown as SSEConnectionContext;
  const gap = adaptPublicRunStreamEventV4({
    eventHeader: "stream.gap", transportCursor: "run-a:1:9-0", generation: 1,
    value: {
      schema: "ai-platform.public-run-stream-control.v4", event_id: "gap-a", run_id: "run-a",
      message_id: null, seq: null, event_type: "stream.gap", stream_incarnation: 1,
      replayable: false, trace_ref: null, causation_event_id: null, emitted_at: "2026-09-28T00:00:00Z",
      payload: {
        reason: "retained_history_unavailable", recovery: "reload_durable_state",
        requested_event_id: "1-0", requested_stream_incarnation: 1, current_stream_incarnation: 1,
        earliest_available_event_id: "8-0", latest_available_event_id: "9-0",
      },
    },
  }, { runId: "run-a", streamIncarnation: 1, generation: 1 });
  assert.ok(gap);
  const args = { sessionId: "session-a", runId: "run-a", messageId: "assistant-a", streamVersion: 1, gap };
  const dependencies = {
    getStatus: async () => ({ session_id: "session-a", run_id: "run-a", status: "running" }),
    connect: async () => {},
  };
  return { context, states, args, dependencies };
}

test("active history timeout releases recovery ownership and permits a later retry", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { context, states, args, dependencies } = gapRecoveryFixture();
  context.hydrateActiveRun = async (_session, _run, _version, _incarnation, _cursor, owner) =>
    recoverRunHistory(() => new Promise<never>(() => {}), owner.controller.signal);
  const pending = recoverReplayGap(context, args, dependencies);
  await flush();
  for (let attempt = 0; attempt < 3; attempt += 1) {
    t.mock.timers.tick(10_000);
    await flush();
    if (attempt < 2) {
      t.mock.timers.tick(1000 * (attempt + 1));
      await flush();
    }
  }
  await pending;
  assert.equal(context.replayGapRecoveryRef?.current, null);
  assert.equal(context.currentRunIdRef.current, "run-a");
  assert.equal(context.acceptedStreamCursorRef?.current.eventId, "run-a:1:1-0");
  assert.equal(states.at(-1), "disconnected");
  let reconnected = false;
  context.hydrateActiveRun = async () => "assistant-a";
  await recoverReplayGap(context, args, { ...dependencies, connect: async () => { reconnected = true; } });
  assert.equal(reconnected, true);
  assert.equal(context.acceptedStreamCursorRef?.current.eventId, "run-a:1:9-0");
});

test("cancelled active history cannot mutate a replacement session", async () => {
  const { context, states, args, dependencies } = gapRecoveryFixture();
  let requestSignal: AbortSignal | undefined;
  let finish: ((messageId: string) => void) | undefined;
  context.hydrateActiveRun = async (_session, _run, _version, _incarnation, _cursor, owner) =>
    recoverRunHistory((signal) => {
      requestSignal = signal;
      return new Promise<string>((resolve) => { finish = resolve; });
    }, owner.controller.signal);
  const pending = recoverReplayGap(context, args, dependencies);
  await flush();
  context.sessionIdRef.current = "session-b";
  context.replayGapRecoveryRef?.current?.controller.abort();
  await pending;
  assert.equal(requestSignal?.aborted, true);
  finish?.("stale-assistant");
  await flush();
  assert.deepEqual(states, ["recovering_gap"]);
  assert.equal(context.streamingMessageIdRef.current, "assistant-a");
});

test("active history authorization failure stops recovery instead of retrying", async () => {
  const { context, args, dependencies } = gapRecoveryFixture();
  let denied = false;
  context.hydrateActiveRun = async () => { throw new ApiRequestError("denied", 403); };
  context.onRunStatusUnavailable = () => { denied = true; return true; };
  await recoverReplayGap(context, args, dependencies);
  assert.equal(denied, true);
  assert.equal(context.replayGapRecoveryRef?.current, null);
});
