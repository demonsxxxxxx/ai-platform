/** Wire events for the independent Word review service. */
export interface WordReviewStreamEvent {
  done: boolean;
  delta: string;
  taskId: string;
  files: unknown[];
}

function parseWordReviewSseBlock(block: string): WordReviewStreamEvent | null {
  const data = block.split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trim())
    .join("\n").trim();
  if (!data) return null;
  if (data === "[DONE]") return { done: true, delta: "", taskId: "", files: [] };
  try {
    const value: unknown = JSON.parse(data);
    const record = value && typeof value === "object" && !Array.isArray(value)
      ? value as Record<string, unknown> : {};
    const delta = [record.delta, record.content, record.text, record.message].find(
      (value): value is string => typeof value === "string",
    ) || "";
    return {
      done: false,
      delta,
      taskId: String(record.task_id || record.taskId || ""),
      files: Array.isArray(record.files) ? record.files : Array.isArray(record.result_files) ? record.result_files : [],
    };
  } catch {
    return { done: false, delta: data, taskId: "", files: [] };
  }
}

/** Decode lines incrementally so CRLF and UTF-8 may cross any byte boundary. */
export async function readWordReviewStream(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: WordReviewStreamEvent) => Promise<void> | void,
  signal?: AbortSignal,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let line = "";
  let block: string[] = [];
  let afterCR = false;
  const checkAbort = () => {
    if (signal?.aborted) throw signal.reason ?? new DOMException("Aborted", "AbortError");
  };
  const abort = () => { void reader.cancel().catch(() => undefined); };
  signal?.addEventListener("abort", abort, { once: true });
  const consume = async (text: string): Promise<boolean> => {
    for (const character of text) {
      checkAbort();
      if (afterCR) {
        afterCR = false;
        if (character === "\n") continue;
      }
      if (character !== "\r" && character !== "\n") { line += character; continue; }
      afterCR = character === "\r";
      if (line) { block.push(line); line = ""; continue; }
      const event = parseWordReviewSseBlock(block.join("\n"));
      block = [];
      if (event) { await onEvent(event); checkAbort(); if (event.done) return true; }
    }
    return false;
  };
  try {
    while (true) {
      checkAbort();
      const result = await reader.read();
      checkAbort();
      if (await consume(result.done ? decoder.decode() : decoder.decode(result.value, { stream: true }))) return;
      if (result.done) break;
    }
    // The service historically emits a final [DONE] without a blank line.
    if (line) block.push(line);
    const finalEvent = parseWordReviewSseBlock(block.join("\n"));
    if (finalEvent) { await onEvent(finalEvent); checkAbort(); if (finalEvent.done) return; }
    throw new Error("审核结果流提前结束，未收到完成回执。请到历史记录核实服务端状态。");
  } finally {
    signal?.removeEventListener("abort", abort);
    try { await reader.cancel(); } catch { /* Preserve the primary result/error. */ }
    reader.releaseLock();
  }
}
