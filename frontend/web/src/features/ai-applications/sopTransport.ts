const SOP_RAGFLOW_API_BASE = "http://10.56.0.211:8080";
const SOP_RAGFLOW_SHARE_URL =
  import.meta.env?.VITE_RAGFLOW_SOP_SHARE_URL?.trim() || "";
const SOP_REQUEST_TIMEOUT = 120_000;

export interface SopRagflowConfig {
  chatId: string;
  auth: string;
}

export function getSopRagflowConfig(): SopRagflowConfig | null {
  try {
    const shareUrl = new URL(SOP_RAGFLOW_SHARE_URL);
    const chatId = shareUrl.searchParams.get("shared_id")?.trim() || "";
    const auth = shareUrl.searchParams.get("auth")?.trim() || "";
    return chatId && auth ? { chatId, auth } : null;
  } catch {
    return null;
  }
}

function parseSopSseBlock(block: string): Record<string, unknown> | null {
  const data = block
    .split(/\r?\n/)
    .filter((line) => line.trimStart().startsWith("data:"))
    .map((line) => line.trimStart().slice(5).trim())
    .filter(Boolean)
    .join("\n");
  if (!data || data === "[DONE]") return null;
  try {
    const event: unknown = JSON.parse(data);
    if (!event || typeof event !== "object") return null;
    const record = event as Record<string, unknown>;
    if (typeof record.code === "number" && record.code !== 0) {
      throw new Error(
        typeof record.message === "string"
          ? record.message
          : "公司知识库返回错误。",
      );
    }
    return record.data && typeof record.data === "object"
      ? (record.data as Record<string, unknown>)
      : null;
  } catch (error) {
    if (error instanceof SyntaxError) return null;
    throw error;
  }
}

async function readSopBlocks(
  response: Response,
  controller: AbortController,
  onBlock: (block: string) => void,
  failureMessage: string,
): Promise<void> {
  const reader = response.body?.getReader();
  let completed = false;
  const abortRead = () => { void reader?.cancel().catch(() => {}); };
  controller.signal.addEventListener("abort", abortRead, { once: true });
  try {
    controller.signal.throwIfAborted();
    if (!response.ok) throw new Error(`${failureMessage}：${response.status}`);
    if (!reader) throw new Error("公司知识库未返回流式响应。");
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const chunk = await reader.read();
      controller.signal.throwIfAborted();
      buffer += decoder.decode(chunk.value || new Uint8Array(), { stream: !chunk.done });
      const blocks = buffer.split(/\n\n|\r\n\r\n/);
      buffer = blocks.pop() || "";
      for (const block of blocks) {
        controller.signal.throwIfAborted();
        onBlock(block);
      }
      if (chunk.done) break;
    }
    if (buffer.trim()) onBlock(buffer);
    completed = true;
  } finally {
    controller.signal.removeEventListener("abort", abortRead);
    if (!completed) controller.abort();
    try {
      if (reader && !completed) await reader.cancel();
    } catch {
      // Cleanup cannot replace the request's result or original failure.
    } finally {
      reader?.releaseLock();
    }
  }
}

async function createSopSession(
  config: SopRagflowConfig,
  controller: AbortController,
): Promise<string> {
  const response = await fetch(
    `${SOP_RAGFLOW_API_BASE}/api/v1/chatbots/${encodeURIComponent(config.chatId)}/completions`,
    {
      method: "POST",
      headers: {
        Accept: "text/event-stream",
        "Content-Type": "application/json",
        Authorization: `Bearer ${config.auth}`,
      },
      body: JSON.stringify({ question: "初始化会话", stream: true }),
      signal: controller.signal,
    },
  );
  let sessionId = "";
  await readSopBlocks(response, controller, block => {
    if (sessionId) return;
    const payload = parseSopSseBlock(block);
    if (typeof payload?.session_id === "string" && payload.session_id) {
      sessionId = payload.session_id;
    }
  }, "公司知识库会话创建失败");
  if (!sessionId) throw new Error("公司知识库未返回会话 ID。");
  return sessionId;
}

export async function streamSopAnswer(
  question: string,
  sessionId: string,
  onAnswer: (answer: string) => void,
  config: SopRagflowConfig,
  signal: AbortSignal,
): Promise<{ answer: string; sessionId: string }> {
  const controller = new AbortController();
  let timedOut = false;
  let completed = false;
  const abort = () => controller.abort(signal.reason);
  signal.addEventListener("abort", abort, { once: true });
  if (signal.aborted) abort();
  const timer = globalThis.setTimeout(() => {
    if (!controller.signal.aborted) {
      timedOut = true;
      controller.abort();
    }
  }, SOP_REQUEST_TIMEOUT);
  try {
    controller.signal.throwIfAborted();
    const activeSessionId =
      sessionId || (await createSopSession(config, controller));
    controller.signal.throwIfAborted();
    const response = await fetch(
      `${SOP_RAGFLOW_API_BASE}/api/v1/chatbots/${encodeURIComponent(config.chatId)}/completions`,
      {
        method: "POST",
        headers: {
          Accept: "text/event-stream",
          "Content-Type": "application/json",
          Authorization: `Bearer ${config.auth}`,
        },
        body: JSON.stringify({
          question,
          stream: true,
          session_id: activeSessionId,
          quote: true,
          reference_metadata: {
            include: true,
            fields: [
              "doc_code",
              "version",
              "effective_date",
              "department",
              "document_type",
            ],
          },
        }),
        signal: controller.signal,
      },
    );
    let answer = "";
    let nextSessionId = activeSessionId;
    const acceptBlock = (block: string) => {
      controller.signal.throwIfAborted();
      const payload = parseSopSseBlock(block);
      if (!payload) return;
      if (typeof payload.session_id === "string") {
        nextSessionId = payload.session_id;
      }
      if (typeof payload.answer === "string") {
        answer = payload.final ? payload.answer : answer + payload.answer;
        onAnswer(answer);
      }
    };

    await readSopBlocks(response, controller, acceptBlock, "公司知识库请求失败");
    if (!answer) throw new Error("公司知识库未返回回答内容。");
    completed = true;
    return { answer, sessionId: nextSessionId };
  } catch (error) {
    if (timedOut) {
      throw new Error("公司知识库回答超时，请稍后重试。");
    }
    if (signal.aborted) throw signal.reason;
    throw error;
  } finally {
    globalThis.clearTimeout(timer);
    signal.removeEventListener("abort", abort);
    if (!completed) controller.abort();

  }
}
