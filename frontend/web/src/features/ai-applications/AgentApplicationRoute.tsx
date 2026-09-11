import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  AlertCircle,
  ArrowLeft,
  BookOpen,
  Clock3,
  ExternalLink,
  FileCheck2,
  FileText,
  Loader2,
  MessageCircle,
  Paperclip,
  Play,
  RotateCcw,
  Send,
  ShieldCheck,
  Sparkles,
  Trash2,
  UploadCloud,
  X,
  type LucideIcon,
} from "lucide-react";

import type { Message } from "../../types";
import { APP_ROUTE_PATHS } from "../../appRouteManifest";
import { resolveInternalAiApplication } from "./aiApplicationCatalog";
import "./wordReviewApplication.css";

const SOP_SUGGESTIONS = [
  "员工报销申请单在 OA 系统如何发起？",
  "费用报销需要提交哪些发票和附件？",
  "询比价采购流程包含哪些步骤？",
  "供应商准入流程是什么？",
];

function formatFileSize(size: number): string {
  if (!size) return "";
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function reviewStatusLabel(status: WordReviewStatus): string {
  return {
    ready: "待处理",
    uploading: "上传中",
    queued: "排队中",
    running: "执行中",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
    timeout: "已超时",
    unknown: "状态未知",
  }[status];
}

const SOP_RAGFLOW_API_BASE = "http://10.56.0.211:8080";
const SOP_RAGFLOW_SHARE_URL =
  import.meta.env?.VITE_RAGFLOW_SOP_SHARE_URL?.trim() || "";
const SOP_REQUEST_TIMEOUT = 120_000;

interface SopRagflowConfig {
  chatId: string;
  auth: string;
}

function getSopRagflowConfig(): SopRagflowConfig | null {
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

async function streamSopAnswer(
  question: string,
  sessionId: string,
  onAnswer: (answer: string) => void,
): Promise<{ answer: string; sessionId: string }> {
  const config = getSopRagflowConfig();
  if (!config) throw new Error("公司知识库访问配置未提供。");

  const controller = new AbortController();
  const timer = globalThis.setTimeout(
    () => controller.abort(),
    SOP_REQUEST_TIMEOUT,
  );
  try {
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
          session_id: sessionId || undefined,
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
    if (!response.ok) {
      throw new Error(`公司知识库请求失败：${response.status}`);
    }
    if (!response.body) throw new Error("公司知识库未返回流式响应。");

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let answer = "";
    let nextSessionId = sessionId;
    let buffer = "";
    const acceptBlock = (block: string) => {
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

    while (true) {
      const chunk = await reader.read();
      buffer += decoder.decode(chunk.value || new Uint8Array(), {
        stream: !chunk.done,
      });
      const blocks = buffer.split(/\n\n|\r\n\r\n/);
      buffer = blocks.pop() || "";
      blocks.forEach(acceptBlock);
      if (chunk.done) break;
    }
    if (buffer.trim()) acceptBlock(buffer);
    if (!answer) throw new Error("公司知识库未返回回答内容。");
    return { answer, sessionId: nextSessionId };
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error("公司知识库回答超时，请稍后重试。");
    }
    throw error;
  } finally {
    globalThis.clearTimeout(timer);
  }
}

function formatTime(value: Date): string {
  return value.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function AppFrame({
  title,
  subtitle,
  icon: Icon,
  status,
  children,
}: {
  title: string;
  subtitle: string;
  icon: LucideIcon;
  status?: ReactNode;
  children: ReactNode;
}) {
  const navigate = useNavigate();
  return (
    <main className="min-h-screen bg-[#f5f8fb] text-[#122235]">
      <header className="border-b border-[#dce5ed] bg-white">
        <div className="mx-auto flex min-h-[72px] w-full max-w-[1440px] items-center gap-4 px-5 sm:px-8">
          <button
            type="button"
            onClick={() => navigate(APP_ROUTE_PATHS.apps)}
            className="flex size-9 shrink-0 items-center justify-center rounded-lg text-[#5d7389] transition-colors hover:bg-[#eef4f8] hover:text-[#244b6c]"
            aria-label="返回 AI 应用"
            title="返回 AI 应用"
          >
            <ArrowLeft size={19} />
          </button>
          <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-[#e8f5f5] text-[#188b8d]">
            <Icon size={21} strokeWidth={1.9} />
          </span>
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-lg font-bold text-[#122235]">{title}</h1>
            <p className="truncate text-xs text-[#6d8295]">{subtitle}</p>
          </div>
          {status}
        </div>
      </header>
      <div className="mx-auto w-full max-w-[1440px] px-5 py-5 sm:px-8 sm:py-7">{children}</div>
    </main>
  );
}

function ConnectionNotice({ failed, message }: { failed: boolean; message?: string }) {
  if (!failed) return null;
  return (
    <div className="connection-notice flex items-start gap-2 rounded-lg border border-[#f1d6a1] bg-[#fff9eb] px-4 py-3 text-sm text-[#8b651c]">
      <AlertCircle size={17} className="mt-0.5 shrink-0" />
      <span>{message || "应用配置暂时无法连接，页面仍可预览；连接平台后即可提交任务。"}</span>
    </div>
  );
}

function AgentStatus({ loading, failed, label = "平台已接入" }: { loading: boolean; failed: boolean; label?: string }) {
  const text = loading ? "正在连接" : failed ? "待连接" : label;
  const color = loading ? "text-[#b27619]" : failed ? "text-[#a36a1b]" : "text-[#13876d]";
  return (
    <span className={`hidden items-center gap-1.5 text-xs font-medium sm:flex ${color}`}>
      <span className={`size-2 rounded-full ${loading ? "bg-[#e3a534]" : failed ? "bg-[#e3a534]" : "bg-[#2eb88a]"}`} />
      {text}
    </span>
  );
}

function MessageBubble({ message }: { message: Message }) {
  const user = message.role === "user";
  return (
    <div className={`flex gap-3 ${user ? "flex-row-reverse" : ""}`}>
      <span className={`flex size-8 shrink-0 items-center justify-center rounded-full ${user ? "bg-[#dcecff] text-[#3b6ea8]" : "bg-[#e6f6f1] text-[#168c73]"}`}>
        {user ? <MessageCircle size={16} /> : <BookOpen size={16} />}
      </span>
      <div className={`max-w-[min(760px,85%)] ${user ? "items-end" : "items-start"} flex flex-col gap-1`}>
        <span className="text-[11px] text-[#8395a6]">{user ? "您" : "SOP 问询助手"}</span>
        <div className={`whitespace-pre-wrap rounded-lg px-4 py-3 text-sm leading-6 ${user ? "bg-[#2f7edb] text-white" : "border border-[#e0e8ee] bg-white text-[#304457]"}`}>
          {message.content || "正在生成回答…"}
          {message.isStreaming && <Loader2 size={14} className="ml-2 inline animate-spin" />}
        </div>
        <span className="text-[11px] text-[#9aa9b6]">{formatTime(message.timestamp)}</span>
      </div>
    </div>
  );
}

function KnowledgeBaseApplication() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [sessionId, setSessionId] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [draft, setDraft] = useState("");
  const [localError, setLocalError] = useState("");
  const configured = getSopRagflowConfig() !== null;

  const clearMessages = () => {
    if (isLoading) return;
    setMessages([]);
    setSessionId("");
    setLocalError("");
  };

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    const text = draft.trim();
    if (!text || isLoading) return;
    if (!configured) {
      setLocalError("公司知识库访问配置未提供。");
      return;
    }

    const requestId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const userMessage: Message = {
      id: `sop-user-${requestId}`,
      role: "user",
      content: text,
      timestamp: new Date(),
    };
    const assistantId = `sop-assistant-${requestId}`;
    const assistantMessage: Message = {
      id: assistantId,
      role: "assistant",
      content: "",
      timestamp: new Date(),
      isStreaming: true,
    };

    setDraft("");
    setLocalError("");
    setIsLoading(true);
    setMessages((current) => [...current, userMessage, assistantMessage]);
    try {
      const result = await streamSopAnswer(text, sessionId, (answer) => {
        setMessages((current) =>
          current.map((message) =>
            message.id === assistantId ? { ...message, content: answer } : message,
          ),
        );
      });
      setSessionId(result.sessionId);
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId
            ? { ...message, content: result.answer, isStreaming: false }
            : message,
        ),
      );
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "SOP 问询失败，请稍后重试。";
      setLocalError(message);
      setMessages((current) =>
        current.map((item) =>
          item.id === assistantId
            ? { ...item, content: message, isStreaming: false }
            : item,
        ),
      );
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <AppFrame
      title="公司 SOP 问询助手"
      subtitle="面向制度、流程、审批与操作指引的公司知识库问答"
      icon={BookOpen}
      status={<AgentStatus loading={isLoading} failed={!configured || Boolean(localError)} label="知识库已接入" />}
    >
      <ConnectionNotice
        failed={!configured}
        message="公司知识库访问配置未提供，请配置 VITE_RAGFLOW_SOP_SHARE_URL。"
      />
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_300px]">
        <section className="flex min-h-[calc(100vh-150px)] flex-col overflow-hidden rounded-lg border border-[#dce5ed] bg-white shadow-[0_2px_8px_rgba(31,58,80,0.04)]">
          <div className="flex items-center justify-between border-b border-[#edf1f4] px-5 py-4">
            <div>
              <h2 className="text-sm font-bold text-[#1b344b]">问询对话</h2>
              <p className="mt-1 text-xs text-[#8294a5]">回答将基于已授权的公司知识库内容</p>
            </div>
            <button
              type="button"
              onClick={clearMessages}
              disabled={messages.length === 0 || isLoading}
              className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs text-[#72869a] hover:bg-[#f2f6f9] disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Trash2 size={14} /> 清空
            </button>
          </div>
          <div className="flex-1 overflow-y-auto px-5 py-6 sm:px-8">
            {messages.length === 0 ? (
              <div className="mx-auto flex max-w-[680px] flex-col items-center pt-12 text-center sm:pt-20">
                <span className="flex size-16 items-center justify-center rounded-full bg-[#e6f6f1] text-[#168c73]"><Sparkles size={28} /></span>
                <h2 className="mt-5 text-xl font-bold text-[#17334b]">您好，我是 SOP 问询助手</h2>
                <p className="mt-2 max-w-[520px] text-sm leading-6 text-[#718598]">我已接入公司知识库，可以帮助您查询制度、审批流程、操作指引和规范要求。</p>
                <div className="mt-8 grid w-full gap-2 sm:grid-cols-2">
                  {SOP_SUGGESTIONS.map((suggestion) => (
                    <button
                      key={suggestion}
                      type="button"
                      onClick={() => setDraft(suggestion)}
                      className="flex min-h-12 items-center gap-2 rounded-lg border border-[#dfe8ee] px-3 text-left text-xs leading-5 text-[#49657c] transition-colors hover:border-[#8fc9c3] hover:bg-[#f2fbf8]"
                    >
                      <MessageCircle size={15} className="shrink-0 text-[#26a58c]" />
                      {suggestion}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="mx-auto flex max-w-[840px] flex-col gap-5">
                {messages.map((message) => <MessageBubble key={message.id} message={message} />)}
              </div>
            )}
          </div>
          <form onSubmit={submit} className="border-t border-[#edf1f4] bg-[#fbfcfd] p-4 sm:p-5">
            {localError && <p className="mb-2 text-xs text-[#c35b50]">{localError}</p>}
            <div className="flex items-end gap-2 rounded-lg border border-[#d4e0e8] bg-white p-2 focus-within:border-[#55aaa0] focus-within:ring-2 focus-within:ring-[#d9f0ed]">
              <textarea
                value={draft}
                onChange={(event) => setDraft(event.target.value.slice(0, 1000))}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                    event.preventDefault();
                    void submit();
                  }
                }}
                rows={2}
                placeholder="请输入制度、流程或审批相关问题…"
                className="min-h-10 flex-1 resize-none border-0 bg-transparent px-2 py-1.5 text-sm text-[#23394c] outline-none placeholder:text-[#a3b0bb]"
              />
              <button
                type="submit"
                disabled={!draft.trim() || isLoading}
                className="flex size-9 shrink-0 items-center justify-center rounded-md bg-[#238f85] text-white transition-colors hover:bg-[#19766e] disabled:cursor-not-allowed disabled:bg-[#c4d8d6]"
                aria-label="发送问题"
                title="发送问题"
              >
                {isLoading ? <Loader2 size={17} className="animate-spin" /> : <Send size={17} />}
              </button>
            </div>
            <p className="mt-2 text-[11px] text-[#9aa9b6]">Enter 发送，Shift + Enter 换行</p>
          </form>
        </section>
        <aside className="h-fit rounded-lg border border-[#dce5ed] bg-white p-5 shadow-[0_2px_8px_rgba(31,58,80,0.04)]">
          <div className="flex items-center gap-2 text-sm font-bold text-[#1b344b]"><ShieldCheck size={17} className="text-[#238f85]" /> 使用说明</div>
          <div className="mt-4 space-y-4 text-xs leading-5 text-[#718598]">
            <p>请输入具体的制度、流程或审批问题，助手会返回答案和相关依据。</p>
            <p>回答仅使用您当前账号有权访问的知识库内容。</p>
            <div className="rounded-md bg-[#f2f8f8] p-3 text-[#4f7075]">当前绑定：公司 SOP 知识库</div>
          </div>
        </aside>
      </div>
    </AppFrame>
  );
}

const WORD_REVIEW_API_BASE = (
  import.meta.env.VITE_WORD_REVIEW_API_TARGET?.trim() || "http://10.56.0.211:8014"
).replace(/\/+$/, "");
const WORD_REVIEW_SKILL_ID = "qa-file-reviewer";
const WORD_REVIEW_AGENT_ID = "qa-word-review";
const WORD_REVIEW_WORK_ID_KEY = "workid";
const WORD_REVIEW_HISTORY_KEY = "wordReviewHistory";
const WORD_REVIEW_MAX_FILE_SIZE = 20 * 1024 * 1024;
const WORD_REVIEW_HISTORY_LIMIT = 200;
const WORD_REVIEW_HISTORY_PAGE_SIZE = 10;
const WORD_REVIEW_REQUEST_TIMEOUT = 8_000;
const WORD_REVIEW_STREAM_TIMEOUT = 15 * 60 * 1_000;

type WordReviewStatus =
  | "ready"
  | "uploading"
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "timeout"
  | "unknown";

interface WordReviewFile {
  name: string;
  label: string;
  path: string;
  key: string;
  fileKey: string;
  downloadUrl?: string;
  previewUrl?: string;
}

interface WordReviewTask {
  localId: string;
  taskId: string;
  sessionId: string;
  workId: string;
  tenantId: string;
  workspaceId: string;
  agentId: string;
  skillId: string;
  name: string;
  size: number;
  fileId: string;
  status: WordReviewStatus;
  progress: number;
  latestProgress: string;
  error: string;
  resultText: string;
  files: WordReviewFile[];
  startedAt: number;
  finishedAt: number;
  durationMs: number;
}

interface WordReviewHistoryItem {
  id: string;
  taskId: string;
  name: string;
  size: number;
  status: WordReviewStatus;
  finishedAt: number;
  durationMs: number;
  summary: string;
  files: WordReviewFile[];
}

interface WordReviewStats {
  total: number;
  todayCount: number;
  daily: Array<{ date: string; count: number }>;
}

interface WordReviewStreamEvent {
  done: boolean;
  delta: string;
  taskId: string;
  files: unknown[];
}

function getStoredWordReviewValue(key: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  try {
    return window.localStorage.getItem(key)?.trim() || fallback;
  } catch {
    return fallback;
  }
}

function getWordReviewContext(sessionId: string) {
  return {
    workId: getStoredWordReviewValue(WORD_REVIEW_WORK_ID_KEY, "default"),
    tenantId: getStoredWordReviewValue("tenant_id", "default"),
    workspaceId: getStoredWordReviewValue("workspace_id", "default"),
    agentId: WORD_REVIEW_AGENT_ID,
    sessionId,
  };
}

function asWordReviewRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function wordReviewErrorDetail(value: unknown): string {
  if (typeof value === "string") return value.trim();
  if (Array.isArray(value)) {
    return value
      .map((item) => wordReviewErrorDetail(item))
      .filter(Boolean)
      .join("; ");
  }
  const record = asWordReviewRecord(value);
  for (const key of ["error_detail", "errorDetail", "error_text", "errorText", "detail", "message", "error"]) {
    const detail = wordReviewErrorDetail(record[key]);
    if (detail) return detail;
  }
  return "";
}

async function wordReviewResponseError(response: Response): Promise<Error> {
  const text = await response.text();
  let detail = "";
  try {
    detail = wordReviewErrorDetail(JSON.parse(text));
  } catch {
    detail = text.trim();
  }
  return new Error((detail || `HTTP ${response.status}`).slice(0, 500));
}

async function requestWordReview(
  path: string,
  init: RequestInit = {},
  timeoutMs = WORD_REVIEW_REQUEST_TIMEOUT,
): Promise<Response> {
  const controller = new AbortController();
  const timer = globalThis.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${WORD_REVIEW_API_BASE}${path}`, {
      ...init,
      signal: controller.signal,
    });
    if (!response.ok) throw await wordReviewResponseError(response);
    return response;
  } finally {
    globalThis.clearTimeout(timer);
  }
}

function uploadWordReviewFile(
  file: File,
  context: ReturnType<typeof getWordReviewContext>,
  onProgress: (progress: number) => void,
): { promise: Promise<{ fileId: string; name: string }>; abort: () => void } {
  const xhr = new XMLHttpRequest();
  const promise = new Promise<{ fileId: string; name: string }>((resolve, reject) => {
    let settled = false;
    const finish = (callback: () => void) => {
      if (settled) return;
      settled = true;
      callback();
    };
    xhr.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
    });
    xhr.addEventListener("load", () => {
      finish(() => {
        if (xhr.status < 200 || xhr.status >= 300) {
          void wordReviewResponseError(
            new Response(xhr.responseText, { status: xhr.status }),
          ).then(reject);
          return;
        }
        try {
          const record = asWordReviewRecord(JSON.parse(xhr.responseText));
          const fileId = String(record.file_id || record.fileId || record.id || record.key || "").trim();
          if (!fileId) throw new Error("上传响应缺少文件编号");
          resolve({
            fileId,
            name: String(record.name || record.filename || file.name),
          });
        } catch (error) {
          reject(error instanceof Error ? error : new Error("上传响应无效"));
        }
      });
    });
    xhr.addEventListener("error", () => finish(() => reject(new Error("上传失败，请检查审核服务连接。"))));
    xhr.addEventListener("timeout", () => finish(() => reject(new Error("上传超时，请稍后重试。"))));
    xhr.addEventListener("abort", () => {
      finish(() => {
        const error = new Error("上传已取消");
        error.name = "AbortError";
        reject(error);
      });
    });

    const form = new FormData();
    form.append("file", file);
    form.append("work_id", context.workId);
    form.append("tenant_id", context.tenantId);
    form.append("workspace_id", context.workspaceId);
    form.append("agent_id", context.agentId);
    form.append("session_id", context.sessionId);
    xhr.open("POST", `${WORD_REVIEW_API_BASE}/api/upload`);
    xhr.timeout = 120_000;
    xhr.send(form);
  });
  return {
    promise,
    abort: () => xhr.abort(),
  };
}

function bytesContainAscii(bytes: Uint8Array, text: string): boolean {
  const needle = Array.from(text, (character) => character.charCodeAt(0));
  for (let start = 0; start <= bytes.length - needle.length; start += 1) {
    if (needle.every((value, index) => bytes[start + index] === value)) return true;
  }
  return false;
}

async function validateWordReviewFile(file: File): Promise<void> {
  if (!file.name.toLowerCase().endsWith(".docx")) throw new Error("仅支持上传 DOCX 文件。");
  if (file.size > WORD_REVIEW_MAX_FILE_SIZE) throw new Error("文件不能超过 20MB。");
  if (file.size === 0) throw new Error("文件为空，请重新选择文件。");
  const bytes = new Uint8Array(await file.arrayBuffer());
  const zip = bytes.length >= 4 && bytes[0] === 0x50 && bytes[1] === 0x4b && [0x03, 0x05, 0x07].includes(bytes[2]) && [0x04, 0x06, 0x08].includes(bytes[3]);
  if (!zip || !bytesContainAscii(bytes, "[Content_Types].xml") || !bytesContainAscii(bytes, "word/document.xml")) {
    throw new Error("上传内容不是有效的 Word DOCX 文件，请确认文件未损坏。");
  }
}

function parseWordReviewSseBlock(block: string): WordReviewStreamEvent | null {
  const data = block
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trim())
    .join("\n")
    .trim();
  if (!data) return null;
  if (data === "[DONE]") return { done: true, delta: "", taskId: "", files: [] };
  try {
    const record = asWordReviewRecord(JSON.parse(data));
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

async function readWordReviewStream(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: WordReviewStreamEvent) => Promise<void> | void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const consume = async (block: string): Promise<boolean> => {
    const event = parseWordReviewSseBlock(block.trim());
    if (!event) return false;
    await onEvent(event);
    return event.done;
  };

  while (true) {
    const result = await reader.read();
    if (result.done) break;
    buffer += decoder.decode(result.value, { stream: true }).replace(/\r\n/g, "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      if (await consume(block)) {
        await reader.cancel();
        return;
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
  buffer += decoder.decode();
  if (buffer.trim()) await consume(buffer);
}

function progressFromReviewText(text: string): number {
  if (/输出结果|执行完成/.test(text)) return 92;
  if (/任务执行中/.test(text)) return 72;
  if (/执行参数|执行脚本/.test(text)) return 58;
  if (/匹配技能|执行策略/.test(text)) return 40;
  if (/初始化|接收请求/.test(text)) return 30;
  return 50;
}

function cleanWordReviewText(text: string): string {
  return String(text || "")
    .replace(/^\[进度\][^\n]*(?:\n|$)/gm, "")
    .replace(/\[\[FILE:[^\]]+\]\]/g, "")
    .replace(/任务ID:[\s\S]*$/m, "")
    .trim();
}

function reviewResultFileLabel(name: string): string {
  const lower = name.toLowerCase();
  if (lower.endsWith(".docx")) return "批注 Word";
  if (lower.endsWith(".txt")) return "详细报告";
  if (lower.endsWith(".md")) return "Markdown 文件";
  return name;
}

function normalizeWordReviewFile(value: unknown): WordReviewFile | null {
  if (typeof value === "string") {
    const path = value.trim();
    if (!path) return null;
    const name = path.replaceAll("\\", "/").split("/").pop() || "review-result";
    return { name, label: reviewResultFileLabel(name), path, key: "", fileKey: "" };
  }
  const record = asWordReviewRecord(value);
  const path = String(record.path || record.file_path || record.filePath || "").trim();
  const key = String(record.key || record.file_key || record.fileKey || "").trim();
  const fileKey = String(record.file_key || record.fileKey || key).trim();
  const downloadUrl = String(record.download_url || record.downloadUrl || "").trim();
  const previewUrl = String(record.preview_url || record.previewUrl || "").trim();
  const rawName = String(record.name || record.filename || record.file_name || record.label || "").trim();
  const name = rawName || path.replaceAll("\\", "/").split("/").pop() || fileKey || "review-result";
  if (!path && !key && !fileKey && !downloadUrl && !previewUrl && !rawName) return null;
  return {
    name,
    label: String(record.label || reviewResultFileLabel(name)),
    path,
    key,
    fileKey,
    ...(downloadUrl ? { downloadUrl } : {}),
    ...(previewUrl ? { previewUrl } : {}),
  };
}

function filesFromWordReviewText(text: string): WordReviewFile[] {
  return Array.from(text.matchAll(/\[\[FILE:([^\]]+)\]\]/g))
    .map((match) => normalizeWordReviewFile(match[1]?.trim() || ""))
    .filter((file): file is WordReviewFile => Boolean(file));
}

function normalizeWordReviewStatus(value: unknown): WordReviewStatus {
  const status = String(value || "").toLowerCase();
  if (["completed", "success", "succeeded", "done"].includes(status)) return "completed";
  if (["failed", "failure", "error"].includes(status)) return "failed";
  if (["running", "processing", "in_progress"].includes(status)) return "running";
  if (["queued", "pending", "waiting"].includes(status)) return "queued";
  if (["uploading", "upload"].includes(status)) return "uploading";
  if (status === "ready") return "ready";
  if (["cancelled", "canceled"].includes(status)) return "cancelled";
  if (status === "timeout") return "timeout";
  return status ? "unknown" : "completed";
}

function wordReviewTimestamp(value: unknown): number {
  if (typeof value === "number") return value > 1e12 ? value : value * 1000;
  if (typeof value !== "string") return 0;
  const timestamp = Date.parse(value);
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

function normalizeWordReviewHistoryItem(value: unknown, index: number): WordReviewHistoryItem {
  const record = asWordReviewRecord(value);
  const taskId = String(record.task_id || record.taskId || "");
  const artifact = asWordReviewRecord(record.artifact);
  const filesSource = Array.isArray(record.files)
    ? record.files
    : Array.isArray(record.result_files)
      ? record.result_files
      : Array.isArray(artifact.files)
        ? artifact.files
        : [];
  const files = filesSource.map(normalizeWordReviewFile).filter((file): file is WordReviewFile => Boolean(file));
  const finishedAt = wordReviewTimestamp(record.finished_at || record.finishedAt || record.completed_at || record.completedAt || record.updated_at || record.updatedAt || record.created_at || record.createdAt);
  const status = normalizeWordReviewStatus(record.status);
  const summary = cleanWordReviewText(String(
    status === "failed"
      ? record.error_detail || record.errorDetail || record.error_text || record.errorText || record.error || record.summary || record.message || ""
      : record.summary || record.result_text || record.resultText || record.message || "",
  )).slice(0, 500);
  return {
    id: String(record.id || taskId || `server-history-${finishedAt || Date.now()}-${index}`),
    taskId,
    name: String(record.name || record.file_name || record.filename || record.fileName || "未命名文档.docx"),
    size: Number(record.size || record.file_size || record.fileSize) || 0,
    status,
    finishedAt,
    durationMs: Number(record.duration_ms || record.durationMs) || 0,
    summary,
    files,
  };
}

function readLocalWordReviewHistory(): WordReviewHistoryItem[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(WORD_REVIEW_HISTORY_KEY);
    if (!raw || raw.length > 1024 * 1024) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed)
      ? parsed.slice(0, WORD_REVIEW_HISTORY_LIMIT).map(normalizeWordReviewHistoryItem)
      : [];
  } catch {
    return [];
  }
}

function mergeWordReviewHistory(
  remote: WordReviewHistoryItem[],
  local: WordReviewHistoryItem[],
): WordReviewHistoryItem[] {
  const result = [...remote];
  const keys = new Set(result.flatMap((item) => [item.id, item.taskId].filter(Boolean)));
  for (const item of local) {
    const key = item.taskId || item.id;
    if (!key || keys.has(key)) continue;
    result.push(item);
    keys.add(key);
  }
  return result.slice(0, WORD_REVIEW_HISTORY_LIMIT);
}

async function fetchWordReviewStats(): Promise<WordReviewStats> {
  const response = await requestWordReview("/api/review/stats/documents?skill_id=qa-file-reviewer&days=30");
  const record = asWordReviewRecord(await response.json());
  const daily = Array.isArray(record.daily)
    ? record.daily.map((item) => {
        const value = asWordReviewRecord(item);
        return { date: String(value.date || ""), count: Number(value.count) || 0 };
      }).filter((item) => item.date)
    : [];
  const today = new Date().toISOString().slice(0, 10);
  return { total: Number(record.total) || 0, todayCount: daily.find((item) => item.date === today)?.count || 0, daily };
}

async function fetchWordReviewHistory(workId: string): Promise<WordReviewHistoryItem[]> {
  const response = await requestWordReview(`/api/review/history?work_id=${encodeURIComponent(workId)}&limit=${WORD_REVIEW_HISTORY_LIMIT}`);
  const record = asWordReviewRecord(await response.json());
  return Array.isArray(record.items) ? record.items.map(normalizeWordReviewHistoryItem) : [];
}
function getWordReviewDownloadUrl(file: WordReviewFile, taskId: string): string {
  const rawUrl = file.downloadUrl;
  if (rawUrl) {
    return /^https?:\/\//i.test(rawUrl)
      ? rawUrl
      : `${WORD_REVIEW_API_BASE}${rawUrl.startsWith("/") ? "" : "/"}${rawUrl}`;
  }
  if (taskId && file.fileKey) {
    return `${WORD_REVIEW_API_BASE}/api/review/history/${encodeURIComponent(taskId)}/download?file_key=${encodeURIComponent(file.fileKey)}`;
  }
  if (file.path) {
    return `${WORD_REVIEW_API_BASE}/api/file/download?path=${encodeURIComponent(file.path)}`;
  }
  return "";
}

function getWordReviewViewUrl(file: WordReviewFile): string {
  if (file.previewUrl) {
    return /^https?:\/\//i.test(file.previewUrl)
      ? file.previewUrl
      : `${WORD_REVIEW_API_BASE}${file.previewUrl.startsWith("/") ? "" : "/"}${file.previewUrl}`;
  }
  if (!file.path) return "";
  return `${WORD_REVIEW_API_BASE}/api/file/view?path=${encodeURIComponent(file.path)}`;
}

function isWordReviewAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

function useWordReviewController() {
  const [tasks, setTasks] = useState<WordReviewTask[]>([]);
  const [history, setHistory] = useState<WordReviewHistoryItem[]>([]);
  const [stats, setStats] = useState<WordReviewStats>({ total: 0, todayCount: 0, daily: [] });
  const [historyError, setHistoryError] = useState("");
  const [historyLoading, setHistoryLoading] = useState(true);
  const [statsLoading, setStatsLoading] = useState(true);
  const [serviceLoading, setServiceLoading] = useState(true);
  const [serviceFailed, setServiceFailed] = useState(false);
  const [selectedHistoryIds, setSelectedHistoryIds] = useState<string[]>([]);
  const [historyPage, setHistoryPage] = useState(1);
  const taskSeqRef = useRef(0);
  const requestAbortRef = useRef<Map<string, () => void>>(new Map());
  const cancelledTaskIdsRef = useRef<Set<string>>(new Set());
  const cancelRequestedRef = useRef<Set<string>>(new Set());
  const cancelSubmittedRef = useRef<Set<string>>(new Set());
  const mountedRef = useRef(true);

  const updateTask = (localId: string, patch: Partial<WordReviewTask>) => {
    if (!mountedRef.current) return;
    setTasks((current) => current.map((task) => (task.localId === localId ? { ...task, ...patch } : task)));
  };

  const persistHistory = (items: WordReviewHistoryItem[]) => {
    try {
      window.localStorage.setItem(WORD_REVIEW_HISTORY_KEY, JSON.stringify(items.slice(0, WORD_REVIEW_HISTORY_LIMIT)));
    } catch {
      // The service remains the source of truth when browser storage is unavailable.
    }
  };

  const recordLocalHistory = (task: WordReviewTask, patch: Partial<WordReviewTask>) => {
    const finalTask = { ...task, ...patch };
    const item: WordReviewHistoryItem = {
      id: finalTask.taskId || finalTask.localId,
      taskId: finalTask.taskId,
      name: finalTask.name,
      size: finalTask.size,
      status: finalTask.status,
      finishedAt: finalTask.finishedAt || Date.now(),
      durationMs: finalTask.durationMs || 0,
      summary: cleanWordReviewText(finalTask.error || finalTask.resultText || finalTask.latestProgress).slice(0, 500),
      files: finalTask.files,
    };
    setHistory((current) => {
      const next = [item, ...current.filter((entry) => entry.id !== item.id && (!item.taskId || entry.taskId !== item.taskId))].slice(0, WORD_REVIEW_HISTORY_LIMIT);
      persistHistory(next);
      return next;
    });
  };

  const refreshHistory = async () => {
    setHistoryLoading(true);
    setHistoryError("");
    try {
      const remote = await fetchWordReviewHistory(getStoredWordReviewValue(WORD_REVIEW_WORK_ID_KEY, "default"));
      if (mountedRef.current) {
        setHistory((current) => mergeWordReviewHistory(remote, current));
        setServiceFailed(false);
      }
      return true;
    } catch {
      if (mountedRef.current) {
        setHistoryError("服务端历史记录暂时无法获取，当前显示本地记录。");
        setServiceFailed(true);
      }
      return false;
    } finally {
      if (mountedRef.current) setHistoryLoading(false);
    }
  };

  const refreshStats = async () => {
    setStatsLoading(true);
    try {
      const value = await fetchWordReviewStats();
      if (mountedRef.current) {
        setStats(value);
        setServiceFailed(false);
      }
      return true;
    } catch {
      if (mountedRef.current) setServiceFailed(true);
      return false;
    } finally {
      if (mountedRef.current) setStatsLoading(false);
    }
  };

  useEffect(() => {
    let active = true;
    setHistory(readLocalWordReviewHistory());
    void (async () => {
      const [statsResult, historyResult] = await Promise.allSettled([
        fetchWordReviewStats(),
        fetchWordReviewHistory(getStoredWordReviewValue(WORD_REVIEW_WORK_ID_KEY, "default")),
      ]);
      if (!active) return;
      if (statsResult.status === "fulfilled") setStats(statsResult.value);
      if (historyResult.status === "fulfilled") {
        setHistory((current) => mergeWordReviewHistory(historyResult.value, current));
      } else {
        setHistoryError("服务端历史记录暂时无法获取，当前显示本地记录。");
      }
      setServiceFailed(statsResult.status === "rejected" && historyResult.status === "rejected");
      setStatsLoading(false);
      setHistoryLoading(false);
      setServiceLoading(false);
    })();
    const abortRequests = requestAbortRef.current;
    return () => {
      active = false;
      mountedRef.current = false;
      for (const abort of abortRequests.values()) abort();
      abortRequests.clear();
    };
  }, []);

  useEffect(() => {
    const pageCount = Math.max(1, Math.ceil(history.length / WORD_REVIEW_HISTORY_PAGE_SIZE));
    setHistoryPage((page) => Math.min(page, pageCount));
    const available = new Set(history.filter((item) => item.files.length > 0).map((item) => item.id));
    setSelectedHistoryIds((ids) => ids.filter((id) => available.has(id)));
  }, [history]);

  const makeTask = (file: File): WordReviewTask => {
    taskSeqRef.current += 1;
    const localId = `${WORD_REVIEW_SKILL_ID}-task-${Date.now()}-${taskSeqRef.current}`;
    const sessionId = `wr-session-${Date.now()}-${taskSeqRef.current}-${Math.random().toString(36).slice(2, 8)}`;
    const context = getWordReviewContext(sessionId);
    return {
      localId,
      taskId: "",
      sessionId,
      workId: context.workId,
      tenantId: context.tenantId,
      workspaceId: context.workspaceId,
      agentId: context.agentId,
      skillId: WORD_REVIEW_SKILL_ID,
      name: file.name,
      size: file.size,
      fileId: "",
      status: "uploading",
      progress: 0,
      latestProgress: "正在上传",
      error: "",
      resultText: "",
      files: [],
      startedAt: 0,
      finishedAt: 0,
      durationMs: 0,
    };
  };

  const uploadTask = async (task: WordReviewTask, file: File) => {
    try {
      await validateWordReviewFile(file);
      if (cancelledTaskIdsRef.current.has(task.localId)) return;
      const handle = uploadWordReviewFile(
        file,
        {
          workId: task.workId,
          tenantId: task.tenantId,
          workspaceId: task.workspaceId,
          agentId: task.agentId,
          sessionId: task.sessionId,
        },
        (progress) => updateTask(task.localId, { progress: Math.max(1, progress), latestProgress: `上传中 ${progress}%` }),
      );
      requestAbortRef.current.set(task.localId, handle.abort);
      const result = await handle.promise;
      requestAbortRef.current.delete(task.localId);
      if (cancelledTaskIdsRef.current.has(task.localId)) return;
      updateTask(task.localId, {
        fileId: result.fileId,
        name: result.name || task.name,
        status: "ready",
        progress: 18,
        latestProgress: "上传完成，等待审核",
      });
      setServiceFailed(false);
    } catch (error) {
      requestAbortRef.current.delete(task.localId);
      if (isWordReviewAbortError(error) || cancelledTaskIdsRef.current.has(task.localId)) return;
      const message = error instanceof Error ? error.message : "上传失败，请检查审核服务连接。";
      const patch: Partial<WordReviewTask> = {
        status: "failed",
        progress: 100,
        error: message,
        latestProgress: "上传失败",
        finishedAt: Date.now(),
      };
      updateTask(task.localId, patch);
      recordLocalHistory(task, patch);
    }
  };

  const selectFiles = (files: FileList | File[]) => {
    const selected = Array.from(files);
    if (selected.length === 0) return;
    for (const file of selected) {
      const task = makeTask(file);
      setTasks((current) => [task, ...current]);
      void uploadTask(task, file);
    }
  };

  const finalizeTask = (task: WordReviewTask, patch: Partial<WordReviewTask>) => {
    const finishedAt = Date.now();
    const finalPatch: Partial<WordReviewTask> = {
      ...patch,
      finishedAt,
      durationMs: task.startedAt ? Math.max(0, finishedAt - task.startedAt) : 0,
    };
    updateTask(task.localId, finalPatch);
    recordLocalHistory(task, finalPatch);
    void refreshHistory();
    void refreshStats();
  };

  const markTaskCancelled = (localId: string, message = "用户已中断任务。") => {
    cancelRequestedRef.current.add(localId);
    updateTask(localId, {
      status: "cancelled",
      progress: 100,
      error: "",
      resultText: message,
      latestProgress: "已中断任务",
    });
  };

  const submitCancellation = async (localId: string, taskId: string) => {
    if (!taskId || cancelSubmittedRef.current.has(localId)) return;
    cancelSubmittedRef.current.add(localId);
    try {
      const response = await requestWordReview(`/api/review/history/${encodeURIComponent(taskId)}/cancel`, { method: "POST" });
      const record = asWordReviewRecord(await response.json().catch(() => ({})));
      const status = String(record.status || "").toLowerCase();
      if (status && status !== "cancelled" && status !== "canceled") throw new Error(String(record.message || "任务已结束，无法中断。"));
      markTaskCancelled(localId, String(record.message || "用户已中断任务。"));
      requestAbortRef.current.get(localId)?.();
    } catch (error) {
      cancelSubmittedRef.current.delete(localId);
      throw error;
    }
  };

  const runReview = async (task: WordReviewTask) => {
    if (!task.fileId || !["ready", "failed", "timeout"].includes(task.status)) return;
    cancelledTaskIdsRef.current.delete(task.localId);
    cancelRequestedRef.current.delete(task.localId);
    cancelSubmittedRef.current.delete(task.localId);
    const startedAt = Date.now();
    const runningTask = { ...task, startedAt, status: "running" as const };
    updateTask(task.localId, {
      status: "running",
      progress: Math.max(24, task.progress),
      latestProgress: "正在初始化审核任务",
      error: "",
      resultText: "",
      startedAt,
      finishedAt: 0,
      durationMs: 0,
      files: [],
    });

    const controller = new AbortController();
    requestAbortRef.current.set(task.localId, () => controller.abort());
    let rawText = "";
    let serverTaskId = task.taskId;
    let timedOut = false;
    let resultFiles: WordReviewFile[] = [];
    const addResultFiles = (values: unknown[]) => {
      const normalized = values.map(normalizeWordReviewFile).filter((file): file is WordReviewFile => Boolean(file));
      resultFiles = [...resultFiles, ...normalized].filter((file, index, all) => {
        const key = file.downloadUrl || file.path || file.fileKey || file.key || file.name;
        return all.findIndex((candidate) => (candidate.downloadUrl || candidate.path || candidate.fileKey || candidate.key || candidate.name) === key) === index;
      });
      updateTask(task.localId, { files: resultFiles });
    };

    const streamTimer = globalThis.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, WORD_REVIEW_STREAM_TIMEOUT);

    try {
      const response = await fetch(`${WORD_REVIEW_API_BASE}/api/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        signal: controller.signal,
        body: JSON.stringify({
          message: "请使用 qa-file-reviewer 对上传的 Word 文档执行公司标准审核，生成带批注 Word。",
          history: [],
          file_id: task.fileId,
          work_id: task.workId,
          client_task_id: task.localId,
          prompt_used: "系统默认审核规则",
          skill_id: task.skillId,
          preferred_skill_id: task.skillId,
          queue_mode: "queued",
          agent_id: task.agentId,
          session_id: task.sessionId,
          tenant_id: task.tenantId,
          workspace_id: task.workspaceId,
          delivery_mode: "word",
        }),
      });
      if (!response.ok) throw await wordReviewResponseError(response);
      if (!response.body) throw new Error("审核服务没有返回流式结果。");
      setServiceFailed(false);

      await readWordReviewStream(response.body, async (event) => {
        if (event.taskId) {
          serverTaskId = event.taskId;
          updateTask(task.localId, { taskId: event.taskId });
          if (cancelRequestedRef.current.has(task.localId)) {
            try {
              await submitCancellation(task.localId, event.taskId);
            } catch (error) {
              cancelRequestedRef.current.delete(task.localId);
              updateTask(task.localId, { latestProgress: error instanceof Error ? error.message : "中断任务失败，请稍后重试。" });
            }
          }
        }
        if (event.files.length > 0) addResultFiles(event.files);
        if (!event.delta) return;
        rawText += event.delta;
        const progressLines = Array.from(event.delta.matchAll(/^\[进度\]\s*(.*)$/gm));
        const latestProgress = progressLines.at(-1)?.[1]?.trim();
        if (latestProgress) {
          updateTask(task.localId, {
            latestProgress,
            progress: Math.min(99, Math.max(24, progressFromReviewText(latestProgress))),
          });
        }
      });

      if (cancelRequestedRef.current.has(task.localId) || /用户已中断任务|任务已取消|任务已取消处理/.test(rawText)) {
        finalizeTask(runningTask, {
          taskId: serverTaskId,
          status: "cancelled",
          progress: 100,
          resultText: "用户已中断任务。",
          latestProgress: "已中断任务",
          files: resultFiles,
        });
        return;
      }
      addResultFiles(filesFromWordReviewText(rawText));
      finalizeTask(runningTask, {
        taskId: serverTaskId,
        status: "completed",
        progress: 100,
        latestProgress: resultFiles.length > 0 ? "审核完成" : "审核完成，但未识别到下载文件",
        resultText: cleanWordReviewText(rawText) || "审核已完成，请下载结果文件。",
        files: resultFiles,
      });
    } catch (error) {
      if (timedOut) {
        const message = "审核任务超时，请到历史记录查看服务端状态。";
        finalizeTask(runningTask, {
          taskId: serverTaskId,
          status: "timeout",
          progress: 100,
          error: message,
          latestProgress: "审核超时",
          resultText: "",
          files: resultFiles,
        });
      } else if (cancelRequestedRef.current.has(task.localId) || isWordReviewAbortError(error)) {
        finalizeTask(runningTask, {
          taskId: serverTaskId,
          status: "cancelled",
          progress: 100,
          resultText: "用户已中断任务。",
          latestProgress: "已中断任务",
          files: resultFiles,
        });
      } else {
        const message = error instanceof Error ? error.message : "审核失败，请检查审核服务连接。";
        finalizeTask(runningTask, {
          taskId: serverTaskId,
          status: "failed",
          progress: 100,
          error: message,
          latestProgress: "审核失败",
          resultText: "",
          files: resultFiles,
        });
      }
    } finally {
      globalThis.clearTimeout(streamTimer);
      requestAbortRef.current.delete(task.localId);
      cancelSubmittedRef.current.delete(task.localId);
    }
  };

  const cancelTask = async (task: WordReviewTask) => {
    if (!task || !["queued", "running"].includes(task.status)) return;
    if (!window.confirm("确认中断该任务？已进入执行中的任务会在服务端可中断点停止。")) return;
    cancelRequestedRef.current.add(task.localId);
    updateTask(task.localId, { latestProgress: task.taskId ? "正在中断任务…" : "等待服务端任务编号，随后中断任务" });
    if (!task.taskId) return;
    try {
      await submitCancellation(task.localId, task.taskId);
    } catch (error) {
      cancelRequestedRef.current.delete(task.localId);
      updateTask(task.localId, { latestProgress: error instanceof Error ? error.message : "中断任务失败，请稍后重试。" });
    }
  };

  const removeTask = (task: WordReviewTask) => {
    if (["queued", "running"].includes(task.status)) return;
    cancelledTaskIdsRef.current.add(task.localId);
    requestAbortRef.current.get(task.localId)?.();
    requestAbortRef.current.delete(task.localId);
    setTasks((current) => current.filter((item) => item.localId !== task.localId));
  };

  const clearTaskList = () => {
    if (tasks.some((task) => ["uploading", "queued", "running"].includes(task.status))) return;
    for (const task of tasks) {
      cancelledTaskIdsRef.current.add(task.localId);
      requestAbortRef.current.get(task.localId)?.();
    }
    requestAbortRef.current.clear();
    setTasks([]);
  };

  const startReadyTasks = () => {
    void Promise.all(tasks.filter((task) => task.status === "ready").map(runReview));
  };

  const retryFailedTasks = () => {
    void Promise.all(tasks.filter((task) => ["failed", "timeout"].includes(task.status) && task.fileId).map(runReview));
  };

  const pageStart = (historyPage - 1) * WORD_REVIEW_HISTORY_PAGE_SIZE;
  const visibleHistory = history.slice(pageStart, pageStart + WORD_REVIEW_HISTORY_PAGE_SIZE);
  const pageCount = Math.max(1, Math.ceil(history.length / WORD_REVIEW_HISTORY_PAGE_SIZE));
  const selectableHistory = visibleHistory.filter((item) => item.files.length > 0);
  const allVisibleHistorySelected = selectableHistory.length > 0 && selectableHistory.every((item) => selectedHistoryIds.includes(item.id));
  const selectedHistory = history.filter((item) => selectedHistoryIds.includes(item.id) && item.files.length > 0);
  const toggleHistorySelection = (item: WordReviewHistoryItem, checked: boolean) => {
    if (!item.files.length) return;
    setSelectedHistoryIds((current) => checked ? Array.from(new Set([...current, item.id])) : current.filter((id) => id !== item.id));
  };
  const togglePageHistorySelection = (checked: boolean) => {
    const pageIds = selectableHistory.map((item) => item.id);
    setSelectedHistoryIds((current) => checked ? Array.from(new Set([...current, ...pageIds])) : current.filter((id) => !pageIds.includes(id)));
  };
  const batchDownloadHistory = async (download: (file: WordReviewFile, taskId: string) => void) => {
    for (const item of selectedHistory) {
      for (const file of item.files) {
        download(file, item.taskId);
        await new Promise((resolve) => window.setTimeout(resolve, 160));
      }
    }
  };

  return {
    tasks,
    history,
    stats,
    historyError,
    historyLoading,
    statsLoading,
    serviceLoading,
    serviceFailed,
    selectedHistoryIds,
    historyPage,
    visibleHistory,
    pageCount,
    allVisibleHistorySelected,
    selectedHistory,
    selectFiles,
    clearTaskList,
    startReadyTasks,
    retryFailedTasks,
    runReview,
    cancelTask,
    removeTask,
    downloadUrl: getWordReviewDownloadUrl,
    viewUrl: getWordReviewViewUrl,
    batchDownloadHistory,
    toggleHistorySelection,
    togglePageHistorySelection,
    setSelectedHistoryIds,
    setHistoryPage,
  };
}

function ReviewStatus({ loading, failed }: { loading: boolean; failed: boolean }) {
  return <AgentStatus loading={loading} failed={failed} label="审核服务已接入" />;
}

function WordReviewApplication() {
  const {
    tasks,
    history,
    stats,
    historyError,
    historyLoading,
    statsLoading,
    serviceLoading,
    serviceFailed,
    selectedHistoryIds,
    historyPage,
    visibleHistory,
    pageCount,
    allVisibleHistorySelected,
    selectedHistory,
    selectFiles,
    clearTaskList,
    startReadyTasks,
    retryFailedTasks,
    runReview,
    cancelTask,
    removeTask,
    downloadUrl,
    viewUrl,
    batchDownloadHistory,
    toggleHistorySelection,
    togglePageHistorySelection,
    setSelectedHistoryIds,
    setHistoryPage,
  } = useWordReviewController();
  const [activeTab, setActiveTab] = useState<"current" | "history">("current");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploading = tasks.some((task) => task.status === "uploading");
  const isLoading = tasks.some((task) => ["queued", "running"].includes(task.status));
  const failureReason = "文档审核服务暂时无法连接，请检查 8014 服务。";

  const downloadResultFile = (file: WordReviewFile, taskId: string) => {
    const url = downloadUrl(file, taskId);
    if (!url) return;
    const link = document.createElement("a");
    link.href = url;
    link.download = file.name || "review-result";
    link.target = "_blank";
    link.rel = "noreferrer";
    document.body.appendChild(link);
    link.click();
    link.remove();
  };

  const reviewStats = [
    { label: "当前排队", value: String(tasks.filter((task) => ["ready", "queued"].includes(task.status)).length) },
    { label: "执行中", value: String(tasks.filter((task) => ["uploading", "running"].includes(task.status)).length) },
    { label: "今日完成", value: String(
      [...history.map((item) => ({
        key: item.taskId || item.id,
        status: item.status,
        finishedAt: item.finishedAt,
        name: item.name,
      })), ...tasks.map((task) => ({
        key: task.taskId || task.localId,
        status: task.status,
        finishedAt: task.finishedAt,
        name: task.name,
      }))].reduce((keys, item) => {
        if (item.status !== "completed" || item.finishedAt < new Date().setHours(0, 0, 0, 0)) return keys;
        keys.add(item.key || `${item.name}-${item.finishedAt}`);
        return keys;
      }, new Set<string>()).size,
    ) },
    { label: "累计审核文档", value: statsLoading ? "…" : String(stats.total), system: true },
    { label: "今日审核文档", value: statsLoading ? "…" : String(stats.todayCount), system: true },
  ];


  return (
    <AppFrame
      title="Word 文档审核"
      subtitle="上传 Word 文档，由独立审核服务生成审核结果"
      icon={FileCheck2}
      status={<ReviewStatus loading={serviceLoading} failed={serviceFailed} />}
    >
      <div className="review-page">
        <ConnectionNotice failed={serviceFailed} message={failureReason} />
        <section className="review-hero">
          <div className="hero-copy">
            <h1>Word文档审核</h1>
            <p>上传文档由 AI 自动进行审核处理，生成可下载结果</p>
          </div>
          <div className="hero-status">
            {reviewStats.map((stat) => (
              <div key={stat.label} className="status-item">
                <span className="status-value">{stat.value}</span>
                <span className="status-label">
                  {stat.label}
                  {stat.system && <span className="status-badge">系统</span>}
                </span>
              </div>
            ))}
          </div>
        </section>

        <section className="review-workbench">
          <div className="upload-panel">
            <div className="panel-header">
              <div>
                <h2>新建审核任务</h2>
                <p>上传需要审核的 Word 文档</p>
              </div>
              {tasks.length > 0 && (
                <button
                  type="button"
                  className="secondary-button"
                  onClick={clearTaskList}
                  disabled={isLoading || uploading}
                >
                  <Trash2 size={14} />
                  清空列表
                </button>
              )}
            </div>

            <button
              type="button"
              className="review-upload"
              onClick={() => fileInputRef.current?.click()}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault();
                selectFiles(event.dataTransfer.files);
              }}
            >
              <UploadCloud size={34} strokeWidth={1.5} />
              <strong>拖拽 Word 文档到这里</strong>
              <span>或点击选择 DOCX 文件</span>
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".docx"
              multiple
              className="sr-only"
              onChange={(event) => {
                if (event.target.files) selectFiles(event.target.files);
                event.target.value = "";
              }}
            />

            {tasks.length > 0 && (
              <div className="upload-list">
                {tasks.map((task) => (
                  <div key={task.localId} className="upload-item">
                    <FileText size={18} className="upload-item-icon" />
                    <div className="upload-item-info">
                      <span className="upload-item-name">{task.name}</span>
                      <span className="upload-item-meta">
                        {task.status === "uploading"
                          ? `上传中 ${task.progress ?? 0}%`
                          : `已上传${formatFileSize(task.size) ? ` · ${formatFileSize(task.size)}` : ""}`}
                      </span>
                    </div>
                    <button
                      type="button"
                      className="icon-button"
                      onClick={() => removeTask(task)}
                      disabled={isLoading && task.status !== "uploading"}
                      aria-label={`移除${task.name}`}
                    >
                      <X size={15} />
                    </button>
                  </div>
                ))}
              </div>
            )}

            {tasks.find((task) => task.error)?.error && <p className="task-error">{tasks.find((task) => task.error)?.error}</p>}
            <div className="primary-actions">
              <button
                type="button"
                className="primary-button"
                onClick={startReadyTasks}
                disabled={isLoading || uploading || !tasks.some((task) => task.status === "ready")}
              >
                {isLoading ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />}
                开始审核
              </button>
              <button
                type="button"
                className="secondary-button"
                onClick={retryFailedTasks}
                disabled={isLoading || uploading || !tasks.some((task) => ["failed", "timeout"].includes(task.status) && task.fileId)}
              >
                <RotateCcw size={15} />
                重试失败任务
              </button>
            </div>
            <p className="upload-reminder">
              <Clock3 size={13} />
              <span>文档审核通常需要约 4 分钟，提交后可稍后回来在当前记录中查看结果。</span>
            </p>
          </div>

          <div className="task-panel">
            <div className="panel-header">
              <div>
                <h2>处理记录</h2>
                <span className="panel-subtitle">共 {tasks.length} 条审核记录</span>
              </div>
              {tasks.length > 0 && (
                <button
                  type="button"
                  className="secondary-button"
                  onClick={clearTaskList}
                  disabled={isLoading}
                >
                  <RotateCcw size={14} />
                  重新开始
                </button>
              )}
            </div>

            <div className="task-tabs">
              <button
                type="button"
                className={activeTab === "current" ? "is-active" : ""}
                onClick={() => setActiveTab("current")}
              >
                当前任务 <span className="task-tab-count">({tasks.length})</span>
              </button>
              <button
                type="button"
                className={activeTab === "history" ? "is-active" : ""}
                onClick={() => setActiveTab("history")}
              >
                历史记录 <span className="task-tab-count">({history.length})</span>
              </button>
            </div>

            {activeTab === "current" ? (
              tasks.length === 0 ? (
                <div className="empty-state">
                  <span className="empty-state-icon"><FileCheck2 size={26} /></span>
                  <span>暂无任务</span>
                </div>
              ) : (
                <div className="task-list">
                  {tasks.map((task) => (
                    <article key={task.localId} className={`task-card is-${task.status}`}>
                      <div className="task-main">
                        <div className="file-icon"><FileText size={20} /></div>
                        <div className="task-content">
                          <div className="task-title-row">
                            <span className="task-name" title={task.name}>{task.name}</span>
                            <span className={`status-tag is-${task.status}`}>{reviewStatusLabel(task.status)}</span>
                          </div>
                          <div className="progress-track"><span style={{ width: `${Math.max(0, Math.min(100, task.progress))}%` }} /></div>
                          <div className="task-meta">
                            <span>Word文档审核</span>
                            {formatFileSize(task.size) && <span>{formatFileSize(task.size)}</span>}
                            <span>{task.latestProgress || reviewStatusLabel(task.status)}</span>
                            {task.durationMs > 0 && <span>耗时 {Math.round(task.durationMs / 1000)} 秒</span>}
                          </div>
                          {task.error && <div className="task-error">{task.error}</div>}
                          {(task.resultText || task.status === "completed" || task.status === "cancelled") && task.status !== "failed" && (
                            <div className="result-summary">{task.resultText || "审核已完成，请查看返回文件。"}</div>
                          )}
                          {task.files.length > 0 && (
                            <div className="result-files">
                              <div className="result-files-title"><Paperclip size={13} />审核结果文件</div>
                              {task.files.map((file) => (
                                <span key={`${file.path}-${file.fileKey}-${file.name}`} className="result-file-group">
                                  {viewUrl(file) && <a className="result-file" href={viewUrl(file)} target="_blank" rel="noreferrer"><ExternalLink size={12} />在线查看</a>}
                                  <a className="result-file" href={downloadUrl(file, task.taskId) || undefined} target="_blank" rel="noreferrer">
                                    <ExternalLink size={12} />{file.label}
                                  </a>
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                      </div>
                      <div className="task-actions">
                        {task.status === "ready" && <button type="button" className="primary-button" onClick={() => void runReview(task)}><Play size={13} />开始审核</button>}
                        {(["failed", "timeout"].includes(task.status) && task.fileId) && <button type="button" className="secondary-button" onClick={() => void runReview(task)}><RotateCcw size={13} />重试</button>}
                        {["queued", "running"].includes(task.status) && <button type="button" className="secondary-button" onClick={() => void cancelTask(task)}><X size={13} />中断任务</button>}
                        <button type="button" className="secondary-button" onClick={() => removeTask(task)} disabled={["uploading", "queued", "running"].includes(task.status)}><X size={13} />移除</button>
                      </div>
                    </article>
                  ))}
                </div>
              )
            ) : historyLoading && history.length === 0 ? (
              <div className="empty-state"><Loader2 size={26} className="animate-spin" /><span>正在加载历史记录…</span></div>
            ) : history.length === 0 ? (
              <div className="empty-state"><span className="empty-state-icon"><Clock3 size={26} /></span><span>暂无历史记录</span></div>
            ) : (
              <>
                <div className="history-tools">
                  <label className="history-select-all"><input type="checkbox" checked={allVisibleHistorySelected} onChange={(event) => togglePageHistorySelection(event.target.checked)} />选择本页</label>
                  <span className="history-count">已选 {selectedHistory.length} 条</span>
                  <button type="button" className="secondary-button" onClick={() => setSelectedHistoryIds([])} disabled={selectedHistoryIds.length === 0}>清空选择</button>
                  <button type="button" className="primary-button" onClick={() => void batchDownloadHistory((file, taskId) => downloadResultFile(file, taskId))} disabled={selectedHistory.length === 0}><ExternalLink size={14} />批量下载</button>
                </div>
                {historyError && <p className="task-error">{historyError}</p>}
                <div className="task-list">
                  {visibleHistory.map((item) => (
                    <article key={item.id} className={`task-card history-card is-${item.status}`}>
                      <div className="task-main history-card-main">
                        <input type="checkbox" checked={selectedHistoryIds.includes(item.id)} disabled={item.files.length === 0} onChange={(event) => toggleHistorySelection(item, event.target.checked)} aria-label={`选择${item.name}`} />
                        <div className="file-icon"><FileText size={20} /></div>
                        <div className="task-content">
                          <div className="task-title-row"><span className="task-name" title={item.name}>{item.name}</span><span className={`status-tag is-${item.status}`}>{reviewStatusLabel(item.status)}</span></div>
                          <div className="task-meta">
                            <span>Word文档审核</span>
                            {formatFileSize(item.size) && <span>{formatFileSize(item.size)}</span>}
                            {item.finishedAt > 0 && <span>完成时间：{new Date(item.finishedAt).toLocaleString("zh-CN")}</span>}
                            {item.durationMs > 0 && <span>耗时：{Math.round(item.durationMs / 1000)} 秒</span>}
                          </div>
                          {item.summary && <div className={`result-summary${["failed", "timeout"].includes(item.status) ? " is-error-summary" : ""}`}>{item.summary}</div>}
                        </div>
                      </div>
                      {item.files.length > 0 && (
                        <div className="task-actions">
                          {item.files.map((file) => (
                            <span key={`${file.path}-${file.fileKey}-${file.name}`} className="result-file-group">
                              {viewUrl(file) && <a className="secondary-button" href={viewUrl(file)} target="_blank" rel="noreferrer"><ExternalLink size={13} />在线查看</a>}
                              <a className="secondary-button" href={downloadUrl(file, item.taskId) || undefined} target="_blank" rel="noreferrer"><ExternalLink size={13} />{file.label}</a>
                            </span>
                          ))}
                        </div>
                      )}
                    </article>
                  ))}
                </div>
                <div className="history-pagination">
                  <button type="button" className="secondary-button" onClick={() => setHistoryPage((page) => Math.max(1, page - 1))} disabled={historyPage <= 1}>上一页</button>
                  <span>第 {historyPage} / {pageCount} 页</span>
                  <button type="button" className="secondary-button" onClick={() => setHistoryPage((page) => Math.min(pageCount, page + 1))} disabled={historyPage >= pageCount}>下一页</button>
                </div>
              </>
            )}
          </div>
        </section>
      </div>
    </AppFrame>
  );
}

export function AgentApplicationRoute() {
  const { appKey } = useParams<{ appKey?: string }>();
  const navigate = useNavigate();
  const application = resolveInternalAiApplication(appKey);

  useEffect(() => {
    if (!application && appKey) {
      navigate(APP_ROUTE_PATHS.apps, { replace: true });
    }
  }, [application, appKey, navigate]);

  if (!application) return null;
  if (appKey === "sop-assistant") return <KnowledgeBaseApplication />;
  return <WordReviewApplication />;
}
