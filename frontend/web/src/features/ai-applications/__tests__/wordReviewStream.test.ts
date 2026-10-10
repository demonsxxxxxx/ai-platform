import assert from "node:assert/strict";
import test from "node:test";
import { readWordReviewStream, type WordReviewStreamEvent } from "../wordReviewStream";
const encoder = new TextEncoder();
function stream(chunks: Uint8Array[]) {
  return new ReadableStream<Uint8Array>({ start(controller) { for (const chunk of chunks) controller.enqueue(chunk); controller.close(); } });
}
async function events(chunks: Uint8Array[]) {
  const result: WordReviewStreamEvent[] = [];
  await readWordReviewStream(stream(chunks), event => { result.push(event); });
  return result;
}
const source = ': heartbeat\r\ndata: {"delta":"中文🙂","task_id":"task-a"}\r\n\r\ndata: {"files":[{"name":"result.docx"}]}\r\n\r\ndata: [DONE]\r\n\r\n';
test("Word CRLF and multibyte UTF-8 are invariant at every byte split and single-byte chunks", async () => {
  const bytes = encoder.encode(source); const expected = await events([bytes]);
  assert.equal(expected.length, 3); assert.equal(expected[0].delta, "中文🙂"); assert.equal(expected[2].done, true);
  for (let split = 0; split <= bytes.length; split++) assert.deepEqual(await events([bytes.slice(0, split), bytes.slice(split)]), expected, `split ${split}`);
  assert.deepEqual(await events(Array.from(bytes, byte => Uint8Array.of(byte))), expected);
});
test("Word supports LF, CR, multiline data, comments, and final DONE without a trailing blank line", async () => {
  for (const newline of ["\n", "\r\n", "\r"]) {
    const result = await events([encoder.encode([': comment', 'data: {"delta":', 'data: "ok"}', '', 'data: [DONE]'].join(newline))]);
    assert.equal(result[0].delta, "ok"); assert.equal(result.at(-1)?.done, true);
  }
});
test("empty EOF and partial data never count as a completion receipt", async () => {
  for (const text of ["", ": heartbeat\n\n", 'data: {"delta":"working"}\n\n', 'data: {"files":[{"name":"result.docx"}]}\n\n']) {
    await assert.rejects(events([encoder.encode(text)]), /未收到完成回执/);
  }
});
test("reader is cancelled and unlocked after terminal, read failure or callback failure", async () => {
  let cancelled = false;
  const body = new ReadableStream<Uint8Array>({ start(controller) { controller.enqueue(encoder.encode("data: [DONE]\n\n")); }, cancel() { cancelled = true; } });
  await readWordReviewStream(body, () => undefined); assert.equal(cancelled, true); assert.equal(body.locked, false);
  const broken = new ReadableStream<Uint8Array>({ start(controller) { controller.error(new Error("network failed")); } });
  await assert.rejects(readWordReviewStream(broken, () => undefined), /network failed/); assert.equal(broken.locked, false);
  const callback = stream([encoder.encode("data: text\n\n")]);
  await assert.rejects(readWordReviewStream(callback, () => { throw new Error("callback failed"); }), /callback failed/); assert.equal(callback.locked, false);
});
test("aborting a pending stream releases its reader and cannot become normal EOF", async () => {
  const controller = new AbortController(); let cancelled = false;
  const body = new ReadableStream<Uint8Array>({ cancel() { cancelled = true; } });
  const reading = readWordReviewStream(body, () => undefined, controller.signal); controller.abort();
  await assert.rejects(reading, { name: "AbortError" }); assert.equal(cancelled, true); assert.equal(body.locked, false);
});
