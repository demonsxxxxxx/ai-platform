import assert from "node:assert/strict";
import test from "node:test";
import { streamSopAnswer } from "../sopTransport";

const config = { chatId: "synthetic-chat", auth: "synthetic-only" };
const encode = (value: unknown) => new TextEncoder().encode(`data: ${JSON.stringify(value)}\n\n`);
const response = (events: unknown[]) => new Response(new ReadableStream<Uint8Array>({ start(controller) { events.forEach(value => controller.enqueue(encode(value))); controller.close(); } }));

test("SOP preserves session creation, cumulative deltas and final replacement", async () => {
  const old = globalThis.fetch;
  const calls: RequestInit[] = [];
  globalThis.fetch = (async (_url, init = {}) => {
    calls.push(init);
    return calls.length === 1 ? response([{ data: { session_id: "session-a" } }]) : response([{ data: { answer: "a" } }, { data: { answer: "b" } }, { data: { answer: "final", final: true, session_id: "session-b" } }]);
  }) as typeof fetch;
  try {
    const answers: string[] = [];
    assert.deepEqual(await streamSopAnswer("question", "", value => answers.push(value), config, new AbortController().signal), { answer: "final", sessionId: "session-b" });
    assert.deepEqual(answers, ["a", "ab", "final"]);
    assert.equal(calls[0].signal, calls[1].signal);
    assert.equal(JSON.parse(String(calls[1].body)).session_id, "session-a");
  } finally { globalThis.fetch = old; }
});

for (const mode of ["business", "read", "success"] as const) {
  test(`SOP ${mode} closes its reader without replacing the original outcome`, async () => {
    const old = globalThis.fetch;
    let cancelled = 0;
    let reads = 0;
    let signal!: AbortSignal;
    const body = new ReadableStream<Uint8Array>({
      pull(controller) {
        if (mode === "read") { controller.error(new Error("synthetic read failure")); return; }
        if (reads++ === 0) controller.enqueue(encode(mode === "business" ? { code: 1, message: "synthetic business failure" } : { data: { answer: "ok" } }));
        else if (mode === "success") controller.close();
      },
      cancel() { cancelled++; throw new Error("synthetic cleanup failure"); },
    });
    globalThis.fetch = (async (_url, init) => { signal = init!.signal!; return new Response(body); }) as typeof fetch;
    try {
      const promise = streamSopAnswer("question", "existing", () => {}, config, new AbortController().signal);
      if (mode === "success") assert.equal((await promise).answer, "ok");
      else await assert.rejects(promise, new RegExp(`synthetic ${mode} failure`));
      assert.equal(body.locked, false);
      assert.equal(signal.aborted, mode !== "success");
      assert.equal(cancelled, mode === "business" ? 1 : 0);
    } finally { globalThis.fetch = old; }
  });
}

test("SOP caller cancellation aborts the pending stream and is not reported as timeout", async () => {
  const old = globalThis.fetch;
  const caller = new AbortController();
  let signal!: AbortSignal;
  let body!: ReadableStream<Uint8Array>;
  globalThis.fetch = (async (_url, init) => {
    signal = init!.signal!;
    body = new ReadableStream({ start(controller) { signal.addEventListener("abort", () => controller.error(signal.reason)); } });
    return new Response(body);
  }) as typeof fetch;
  try {
    const pending = streamSopAnswer("question", "existing", () => {}, config, caller.signal);
    await Promise.resolve();
    caller.abort();
    await assert.rejects(pending, { name: "AbortError" });
    assert.equal(signal.aborted, true);
    assert.equal(body.locked, false);
  } finally { globalThis.fetch = old; }
});

test("SOP timeout aborts with the existing timeout message", async () => {
  const oldFetch = globalThis.fetch;
  const oldTimer = globalThis.setTimeout;
  let timeout!: () => void;
  globalThis.setTimeout = ((fn: () => void) => { timeout = fn; return 0; }) as unknown as typeof setTimeout;
  globalThis.fetch = ((_url, init) => new Promise((_resolve, reject) => { init!.signal!.addEventListener("abort", () => reject(init!.signal!.reason)); })) as typeof fetch;
  try {
    const pending = streamSopAnswer("question", "existing", () => {}, config, new AbortController().signal);
    timeout();
    await assert.rejects(pending, /公司知识库回答超时/);
  } finally { globalThis.fetch = oldFetch; globalThis.setTimeout = oldTimer; }
});

test("SOP cancellation releases a blocked initialized reader even when its source ignores abort", async () => {
  const old = globalThis.fetch;
  const caller = new AbortController();
  let cancelled = 0;
  const body = new ReadableStream<Uint8Array>({ cancel() { cancelled++; } });
  globalThis.fetch = (async () => new Response(body)) as typeof fetch;
  try {
    const pending = streamSopAnswer("question", "existing", () => {}, config, caller.signal);
    await Promise.resolve();
    caller.abort();
    await assert.rejects(pending, { name: "AbortError" });
    assert.equal(cancelled, 1);
    assert.equal(body.locked, false);
  } finally { globalThis.fetch = old; }
});

for (const sessionId of ["", "existing"]) {
  test(`SOP cancels rejected HTTP bodies during ${sessionId ? "answer" : "initialization"}`, async () => {
    const old = globalThis.fetch;
    let cancelled = 0;
    const body = new ReadableStream<Uint8Array>({ cancel() { cancelled++; } });
    globalThis.fetch = (async () => new Response(body, { status: 503 })) as typeof fetch;
    try {
      await assert.rejects(streamSopAnswer("question", sessionId, () => {}, config, new AbortController().signal), /503/);
      assert.equal(cancelled, 1);
      assert.equal(body.locked, false);
    } finally { globalThis.fetch = old; }
  });
}
