// Synthetic preview entry: real App/routes/ChatView with mocked API, no login.
// Every response below is fabricated: no model, backend, credential or real session.
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import "katex/dist/katex.min.css";
import "../../src/i18n";
import App from "../../src/App";
import "../../src/styles/tailwind.css";
import "../../src/styles/base.css";
import "../../src/styles/animations.css";
import "../../src/styles/components.css";
import "../../src/styles/auth.css";
import "../../src/styles/chat.css";
import "../../src/styles/skill.css";
import "../../src/styles/skill-management-table.css";
import "../../src/styles/card-base.css";
import "../../src/styles/welcome.css";
import "../../src/styles/syntax-highlight.css";
import "../../src/styles/markdown.css";
import "../../src/styles/utilities.css";
import { AuthProvider } from "../../src/hooks/useAuth";
import { ModelCatalogProvider } from "../../src/contexts/ModelCatalogContext";

const theme = new URLSearchParams(location.search).get("theme") === "dark" ? "dark" : "light";
localStorage.setItem("ai-platform-theme", theme);
localStorage.setItem("ai-platform-sidebar-collapsed", "false");
localStorage.setItem("language", "zh");

const originalFetch = window.fetch.bind(window);
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });

const SESSION_ID = "session-preview-1";
const INCARNATION = 1;
const EMITTED = "2026-10-20T07:00:00Z";
let cursorMs = 1700000000000;

interface PreviewTurn {
  runId: string;
  question: string;
  answer: string;
  suggestions: string[];
}

// Synthetic model questions so the surface is observable without any real call.
// Each turn labels its own set so a stale row can never pass as a fresh one.
function syntheticSuggestions(turnNumber: number): string[] {
  return [
    `第${turnNumber}轮：把任务清单拆成一周的排期？`,
    `第${turnNumber}轮：补充每条任务的验收标准？`,
    `第${turnNumber}轮：列出需要我确认的风险点？`,
  ];
}

const turns: PreviewTurn[] = [];

// One restored turn, so the page opens on a conversation; restored history never
// requests follow-up questions — only a reply that settles here does.
addTurn(
  "帮我把这段需求整理成任务清单",
  "收到，我来帮你整理。\n\n**任务清单**\n\n1. 明确目标与验收标准\n2. 拆解为可执行步骤\n3. 标注负责人与截止时间",
);

function addTurn(question: string, answer: string, suggestions?: string[]): PreviewTurn {
  const turn = {
    runId: `run-preview-${turns.length + 1}`,
    question,
    answer,
    suggestions: suggestions ?? syntheticSuggestions(turns.length + 1),
  };
  turns.push(turn);
  return turn;
}

function eventFrame(runId: string, eventType: string, seq: number, payload: Record<string, unknown>, messageId: string | null) {
  cursorMs += 1;
  return {
    id: `${runId}:${INCARNATION}:${cursorMs}-0`,
    event: eventType,
    data: JSON.stringify({
      schema: "ai-platform.public-run-stream-event.v4",
      event_id: `${runId}-event-${seq}`,
      run_id: runId,
      message_id: messageId,
      seq,
      event_type: eventType,
      stream_incarnation: INCARNATION,
      replayable: true,
      trace_ref: null,
      causation_event_id: null,
      emitted_at: EMITTED,
      payload,
    }),
  };
}

function streamBody(turn: PreviewTurn) {
  const messageId = `${turn.runId}-assistant`;
  const frames = [
    eventFrame(turn.runId, "message.started", 1, {}, messageId),
    eventFrame(turn.runId, "message.delta", 2, { delta: turn.answer }, messageId),
    eventFrame(turn.runId, "message.completed", 3, { delta_count: 1, text_length: turn.answer.length }, messageId),
    eventFrame(turn.runId, "run.succeeded", 4, { terminal_event_id: `${turn.runId}-terminal`, hydrate_required: true }, null),
    {
      id: `${turn.runId}:${INCARNATION}:${++cursorMs}-0`,
      event: "stream.end",
      data: JSON.stringify({
        schema: "ai-platform.public-run-stream-control.v4",
        event_id: `${turn.runId}-end`,
        run_id: turn.runId,
        message_id: null,
        seq: null,
        event_type: "stream.end",
        stream_incarnation: INCARNATION,
        replayable: true,
        trace_ref: null,
        causation_event_id: null,
        emitted_at: EMITTED,
        payload: { terminal_event_id: `${turn.runId}-terminal` },
      }),
    },
  ];
  const body = frames.map((frame) => `id: ${frame.id}\nevent: ${frame.event}\ndata: ${frame.data}\n\n`).join("");
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-store" } });
}

function historyEvents() {
  return turns.flatMap((turn) => {
    const messageId = `${turn.runId}-assistant`;
    return [
      {
        id: `${turn.runId}-user`,
        run_id: turn.runId,
        event_type: "user:message",
        timestamp: EMITTED,
        data: { content: turn.question },
      },
      {
        id: `${turn.runId}-started`,
        run_id: turn.runId,
        event_type: "message.started",
        timestamp: EMITTED,
        sequence: 1,
        data: { event_id: `${turn.runId}-started`, message_id: messageId, run_id: turn.runId, sequence: 1, stream_incarnation: INCARNATION },
      },
      {
        id: `${turn.runId}-delta`,
        run_id: turn.runId,
        event_type: "message.delta",
        timestamp: EMITTED,
        sequence: 2,
        data: {
          event_id: `${turn.runId}-delta`,
          message_id: messageId,
          run_id: turn.runId,
          sequence: 2,
          stream_incarnation: INCARNATION,
          delta: turn.answer,
        },
      },
      {
        id: `${turn.runId}-terminal`,
        run_id: turn.runId,
        event_type: "run_succeeded",
        timestamp: EMITTED,
        data: { status: "succeeded" },
      },
    ];
  });
}

function lastTurn(): PreviewTurn | undefined {
  return turns[turns.length - 1];
}

function markSyntheticPreview() {
  // A screen-visible marker: this page is fabricated data, not a real conversation.
  const banner = document.createElement("div");
  banner.setAttribute("data-synthetic-preview-banner", "true");
  banner.textContent = "合成预览 · 数据与后续建议均为本地模拟，不调用真实模型";
  Object.assign(banner.style, {
    position: "fixed",
    top: "0",
    left: "0",
    right: "0",
    zIndex: "9999",
    padding: "4px 12px",
    font: "12px/1.5 system-ui, sans-serif",
    textAlign: "center",
    color: theme === "dark" ? "#fde68a" : "#78350f",
    background: theme === "dark" ? "rgba(120, 53, 15, 0.9)" : "rgba(254, 243, 199, 0.95)",
    borderBottom: "1px solid rgba(120, 53, 15, 0.35)",
  });
  document.body.appendChild(banner);
  document.documentElement.style.setProperty("--synthetic-preview-offset", "26px");
  window.addEventListener("load", () => {
    const root = document.getElementById("root");
    if (root) root.style.paddingTop = "26px";
  });
}

window.fetch = async (input: RequestInfo | URL, init: RequestInit = {}) => {
  const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, location.origin);
  const method = String(init.method || "GET").toUpperCase();
  (window as unknown as { __log?: string[] }).__log = [...((window as unknown as { __log?: string[] }).__log ?? []), `${method} ${url.pathname}`];
  if (url.pathname === "/api/ai/auth/me") {
    return json({
      user_id: "preview-user", user_name: "preview-user", display_name: "预览用户",
      tenant_id: "preview-tenant", roles: ["admin"], is_admin: true, source: "cookie_session",
      permissions: [
        "chat:read", "chat:write", "session:read", "session:write", "file:upload",
        "file:upload:image", "file:upload:document", "file:upload:video", "file:upload:audio",
        "skill:read", "skill:admin", "agent_profile:admin", "model:admin",
      ],
    });
  }
  if (url.pathname === "/api/ai/auth/bootstrap" && method === "POST") {
    return json({ status: "ready", protocol_version: 2, generation: 1 });
  }
  if (url.pathname === "/api/agent/models/available") {
    return json({
      models: [
        { id: "model-preview", value: "model-preview", label: "预览模型", provider: "synthetic" },
        { id: "model-preview-2", value: "model-preview-2", label: "预览模型 2", provider: "synthetic" },
      ],
      count: 2, enabled_count: 2, default_model_id: "model-preview",
    });
  }
  if (url.pathname === "/api/upload/config") {
    return json({ enabled: true, provider: "s3", maxFiles: 32, uploadLimitsBytes: { image: 20971520, video: 104857600, audio: 52428800, document: 52428800 } });
  }
  if (url.pathname === "/api/ai/chat/sessions") return json({ sessions: [], next_cursor: null });
  if (url.pathname === "/api/sessions") return json({ sessions: [], total: 0, skip: 0, limit: 20, has_more: false });
  if (url.pathname === "/api/skills" || url.pathname === "/api/skills/") {
    return json({ skills: [], total: 0, skip: 0, limit: 20, available_tags: [], effective_permissions: [], effective_permissions_known: true, catalog_read_resolved: true });
  }
  if (url.pathname === "/api/mcp/chat-tools") return json({ tools: [], count: 0, unavailable: [] });
  if (url.pathname === "/api/profile-drive/status") return json({ status: "disconnected", connected: false, reauthRequired: false });
  if (url.pathname === "/api/chat/stream" && method === "POST") {
    const body = init.body ? JSON.parse(String(init.body)) : {};
    const question = typeof body.message === "string" && body.message.trim() ? body.message.trim() : "（未填写问题）";
    const turn = addTurn(question, `收到「${question}」。\n\n**合成回答**：这是本地模拟的任务清单。\n\n1. 明确目标与验收标准\n2. 拆解为可执行步骤\n3. 标注负责人与截止时间`);
    return json({ session_id: SESSION_ID, run_id: turn.runId, trace_id: `trace-${turn.runId}`, status: "queued" });
  }
  if (url.pathname === `/api/chat/sessions/${SESSION_ID}/stream`) {
    const requested = url.searchParams.get("run_id");
    const turn = turns.find((item) => item.runId === requested) ?? lastTurn();
    if (!turn) return json({ detail: "preview_no_turn" }, 404);
    return streamBody(turn);
  }
  // Synthetic follow-up suggestions: a fixed local answer, never a model call.
  if (url.pathname === "/api/ai/chat/follow-up-suggestions" && method === "POST") {
    const body = init.body ? JSON.parse(String(init.body)) : {};
    const turn = turns.find((item) => item.runId === body.run_id);
    return json({ suggestions: turn ? turn.suggestions : [] });
  }
  if (url.pathname === `/api/ai/chat/sessions/${SESSION_ID}`) {
    return json({ session_id: SESSION_ID, workspace_id: "preview-workspace", agent_id: "generic", title: "预览会话", purpose: "conversation", agent_conversation: null });
  }
  if (url.pathname === `/api/sessions/${SESSION_ID}/events`) {
    return json({
      session_id: SESSION_ID,
      run_id: lastTurn()?.runId ?? null,
      events: historyEvents(),
      next_cursor: null,
      terminal_run_statuses: Object.fromEntries(turns.map((turn) => [turn.runId, "succeeded"])),
    });
  }
  if (url.pathname === `/api/sessions/${SESSION_ID}`) {
    return json({ id: SESSION_ID, agent_id: "generic", created_at: EMITTED, updated_at: EMITTED, is_active: true, name: "预览会话", metadata: {} });
  }
  if (url.pathname === `/api/ai/chat/sessions/${SESSION_ID}/files`) return json({ session_id: SESSION_ID, files: [] });
  if (url.pathname === `/api/chat/sessions/${SESSION_ID}/status`) {
    return json({ session_id: SESSION_ID, run_id: lastTurn()?.runId ?? null, status: "succeeded", raw_status: "succeeded" });
  }
  if (url.pathname.startsWith("/api/ai/runs/") && url.pathname.endsWith("/inputs")) {
    return json({ run_id: url.pathname.split("/")[4], state: "inactive", inputs: [], questions: [] });
  }
  if (url.pathname.startsWith("/api/ai/runs/") && url.pathname.endsWith("/inputs/history")) {
    return json({ run_id: url.pathname.split("/")[4], inputs: [], questions: [], has_more: false });
  }
  if (url.pathname === `/api/ai/sessions/${SESSION_ID}/run-inputs`) {
    return json({ session_id: SESSION_ID, runs: [], has_more: false, next_before_run_id: null });
  }
  if (url.pathname.startsWith("/api/chat/submissions/")) {
    return json({ detail: "preview_submission_unstubbed" }, 404);
  }
  if (url.pathname.startsWith("/api/")) return json({ detail: "preview_unstubbed" }, 501);
  return originalFetch(input, init);
};

markSyntheticPreview();

createRoot(document.getElementById("root")!).render(
  <MemoryRouter initialEntries={[`/chat/${SESSION_ID}`]}>
    <AuthProvider>
      <ModelCatalogProvider>
        <App />
      </ModelCatalogProvider>
    </AuthProvider>
  </MemoryRouter>,
);
