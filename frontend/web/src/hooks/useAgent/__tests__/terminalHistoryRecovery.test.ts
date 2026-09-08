import assert from "node:assert/strict";
import test from "node:test";
import { recoverTerminalHistory } from "../terminalHistoryRecovery";

const flush = async () => { for (let i = 0; i < 10; i += 1) await Promise.resolve(); };

test("terminal history retries a transient failure and returns the actual result", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let calls = 0;
  const result = recoverTerminalHistory(async () => {
    if (++calls === 1) throw new Error("temporary");
    return { events: ["synthetic final"] };
  }, new AbortController().signal);
  await flush();
  assert.equal(calls, 1);
  t.mock.timers.tick(1000);
  assert.deepEqual(await result, { events: ["synthetic final"] });
  assert.equal(calls, 2);
});

test("terminal history cancels both stalled requests and retry waits", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  for (const stalled of [false, true]) {
    const owner = new AbortController();
    let calls = 0;
    let requestSignal: AbortSignal | undefined;
    const result = recoverTerminalHistory(async (signal) => {
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

test("terminal history never retries permission failures", async () => {
  let calls = 0;
  await assert.rejects(recoverTerminalHistory(async () => {
    calls += 1;
    throw Object.assign(new Error("denied"), { status: 403 });
  }, new AbortController().signal));
  assert.equal(calls, 1);
});

test("stalled terminal requests time out and exhaust exactly three attempts", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const signals: AbortSignal[] = [];
  const result = recoverTerminalHistory((signal) => {
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
