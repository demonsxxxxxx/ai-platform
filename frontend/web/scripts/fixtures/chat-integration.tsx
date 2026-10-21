// Synthetic browser fixture: real ChatView/providers/renderers, no model or backend execution.
import { useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { ToastHost } from "@lobehub/ui/base-ui";
import { ChatView } from "../../src/components/layout/AppContent/ChatView";
import { WorkbenchShell } from "../../src/components/workbench/WorkbenchShell";
import { ThemeProvider } from "../../src/contexts/ThemeContext";
import { LobeUiProvider } from "../../src/contexts/LobeUiProvider";
import { ModelCatalogProvider } from "../../src/contexts/ModelCatalogContext";
import { AuthProvider, useAuth } from "../../src/hooks/useAuth";
import { useFileUpload } from "../../src/hooks/useFileUpload";
import { createSelectedSkillTaskState } from "../../src/hooks/useSelectedSkillTask";
import type { RunInputQuestionBatch, RunInputsProjection } from "../../src/services/api/session";
import type { RunInputsController } from "../../src/hooks/useAgent/types";
import type { Message, MessageAttachment, MessagePart } from "../../src/types";
import "../../src/i18n";
import "katex/dist/katex.min.css";
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

const noop = () => {};
const ready = async () => {};
const toolParts: MessagePart[] = (["completed", "failed", "denied", "started"] as const).map((status, index) => ({
  type: "tool", id: `operation-${index}`, name: "private executor name", args: {},
  status, public_operation_id: `operation-${index}`, public_category: "read", duration_ms: 1250,
}));
const finalText = "已整理文件。最终公开回答。\n\n**重点**：文件已归档。\n\n数学校验：$a^2 + b^2 = c^2$。\n\n[打开安全参考](https://example.com/reference)\n\n| 文件 | 状态 |\n| --- | --- |\n| 报告 | 已归档 |\n\n```text\n归档完成\n```\n\n```mermaid\nflowchart LR\n  A[读取] --> B[归档]\n```";
const longToolLabelParts: MessagePart[] = (["completed", "started"] as const).map((status, index) => ({
  type: "tool", id: `long-operation-${index}`, name: "private executor name", args: {}, status,
  public_operation_id: `long-operation-${index}`, public_category: "skill", public_display_name: "公开工具名称这是一个用于检查截断和布局的非常长的工具名称", duration_ms: 1250,
}));
const nestedSubagentParts: MessagePart = {
  type: "subagent", agent_id: "public-operation-root", public_operation_id: "public-operation-root", agent_name: "private agent name",
  input: "private input", depth: 1, status: "complete", duration_ms: 2200, progress_percent: 100,
  parts: [{
    type: "subagent", agent_id: "public-operation-child", public_operation_id: "public-operation-child", agent_name: "private child name",
    input: "private child input", depth: 2, status: "complete", parent_agent_id: "public-operation-root", duration_ms: 1400, progress_percent: 100,
    parts: [{
      type: "subagent", agent_id: "public-operation-grandchild", public_operation_id: "public-operation-grandchild", agent_name: "private grandchild name",
      input: "private grandchild input", depth: 3, status: "complete", parent_agent_id: "public-operation-child", duration_ms: 700, progress_percent: 100,
      parts: [{ type: "text", content: "公开协作结果。" }],
    }],
  }],
};
const todoPart: MessagePart = { type: "todo", isStreaming: false, items: [
  { content: "读取授权资料", status: "completed" },
  { content: "整理长文件名的公开结果", status: "in_progress", activeForm: "正在整理长文件名的公开结果" },
  { content: "发送归档产物", status: "pending" },
] };
const artifactPart: MessagePart = {
  type: "artifact", artifact_id: "artifact-public-report", artifact_type: "document", label: "公开归档报告（这是一个用于检查截断和布局并确认授权文件下载入口仍然可访问的非常长的文件名）.pdf",
  content_type: "application/pdf", size_bytes: 2048, status: "ready", download_url: "/api/ai/artifacts/artifact-public-report/download",
  preview_url: "/api/ai/artifacts/artifact-public-report/preview", created_at: "2026-01-01T00:00:00Z",
};
const questionBatch: RunInputQuestionBatch = {
  question_id: "question-synthetic", status: "pending", created_at: "2026-01-01T00:00:00Z",
  questions: [
    { key: "priority", header: "单选", question: "选择处理优先级", multiSelect: false, options: [{ key: "normal", label: "普通", description: "按默认顺序处理", recommended: true }, { key: "urgent", label: "紧急", description: "优先处理" }] },
    { key: "formats", header: "多选", question: "选择输出格式", multiSelect: true, options: [{ key: "pdf", label: "PDF", description: "便于归档" }, { key: "docx", label: "DOCX", description: "便于编辑" }, { key: "markdown", label: "Markdown", description: "便于复用" }, { key: "csv", label: "CSV", description: "便于处理数据" }] },
    { key: "note", header: "自由文本", question: "补充说明", multiSelect: false, options: [] },
  ],
};
const performanceToolParts: MessagePart[] = Array.from({ length: 50 }, (_, index) => ({
  ...longToolLabelParts[index % 2], id: `performance-tool-${index}`, public_operation_id: `performance-tool-${index}`,
}));
const LONG_RECEIPT_TEXT = "已结束任务的长回执说明：" + "这一段用于验证关闭回执在受限区域内可以滚动读取。".repeat(24) + "【回执末尾哨兵】";
function longHistoryProjection(index: number): RunInputsProjection {
  return {
    run_id: `run-synthetic-history-${index}`, state: "inactive",
    questions: [{ ...questionBatch, question_id: "question-history", status: "closed" }],
    inputs: [
      { input_id: "history-text", kind: "text", text: LONG_RECEIPT_TEXT, question_id: null, answers: null, status: "queued", created_at: "2026-01-01T00:00:00Z" },
      { input_id: "history-answer", kind: "answer", text: null, question_id: "question-history",
        answers: { priority: "normal", formats: ["pdf", "docx"], note: { text: LONG_RECEIPT_TEXT } },
        status: "closed", source: "timeout_default", created_at: "2026-01-01T00:05:00Z" },
    ],
  };
}
const fixtureState = { sends: 0, stops: 0, finalText, lastAnswers: {} as Record<string, unknown>, showGeneric: noop, showQuestions: noop, showLongQuestion: noop, attach: noop, advance: noop as (text?: string) => void, synchronize: noop, finish: noop, fail: noop, resolveQuestions: noop as () => void, showHistoryWithReceipts: noop, clearHistory: noop, clearQuestion: noop, restoreHistory: noop, performanceStream: noop as (count: number) => Promise<number> };
function performanceMessages(): Message[] {
  return Array.from({ length: 500 }, (_, index) => {
    const timestamp = new Date(index * 2000);
    return [
      { id: `performance-user-${index}`, runId: `performance-run-${index}`, role: "user", content: `固定性能问题 ${index}`, timestamp },
      { id: `performance-assistant-${index}`, runId: `performance-run-${index}`, role: "assistant", isStreaming: index === 499, content: `固定性能答复 ${index}`, timestamp: new Date(timestamp.getTime() + 1), parts: [{ type: "text", content: `固定性能答复 ${index}` }, ...(index === 499 ? performanceToolParts : [])] },
    ] as Message[];
  }).flat();
}
Object.assign(window, { __chatIntegration: fixtureState });

export function Fixture() {
  const performanceMode = new URLSearchParams(window.location.search).has("performance");
  const [messages, setMessages] = useState<Message[]>(() => performanceMode ? performanceMessages() : []);
  const [running, setRunning] = useState(performanceMode);
  const [settling, setSettling] = useState(false);
  const [historyRevision, setHistoryRevision] = useState(0);
  const [generic, setGeneric] = useState(false);
  const [model, setModel] = useState("model-a");
  const [questionProjection, setQuestionProjection] = useState<RunInputsProjection | null>(null);
  const [historyProjections, setHistoryProjections] = useState<RunInputsProjection[]>([]);
  const [attachments, setAttachments] = useState<MessageAttachment[]>([]);
  const uploadControls = useFileUpload({ attachments, onAttachmentsChange: setAttachments });
  const runInputs: RunInputsController = {
    sessionId: "session-synthetic", runId: "run-synthetic-1", projection: questionProjection,
    history: [...historyProjections, ...(questionProjection ? [questionProjection] : [])],
    historyIsLoading: false, loadFailed: false, historyLoadFailed: false, historyHasMore: false, isLoading: false, isClosed: false,
    submissionError: null, pendingSubmission: null, lastAcceptedSubmission: null, loadMoreHistory: async () => false, refreshHistory: async () => true,
    refresh: async () => true, retire: noop, submitText: async () => true,
    submitAnswers: async (questionId, answers) => {
      fixtureState.lastAnswers = answers;
      setQuestionProjection((previous) => previous && previous.questions.some((question) => question.question_id === questionId) ? {
        ...previous, questions: previous.questions.map((question) => question.question_id === questionId ? { ...question, status: "answered" as const } : question),
      } : previous);
      return true;
    },
    retryPendingSubmission: async () => true,
  };
  const updateActiveMessage = useCallback((update: (message: Message) => Message) => setMessages((current) => current.map((message) =>
    message.id === `assistant-synthetic-${fixtureState.sends}` ? update(message) : message)), []);
  useEffect(() => {
    fixtureState.advance = (text = "正在整理文件。") => updateActiveMessage((message) => ({
      ...message, content: text, parts: [...toolParts, { type: "text", content: text }],
    }));
    fixtureState.synchronize = () => {
      setSettling(true);
      updateActiveMessage((message) => ({ ...message, isStreaming: false, isSynchronizing: true }));
    };
    fixtureState.finish = () => {
      setRunning(false);
      setSettling(false);
      updateActiveMessage((message) => ({ ...message, isStreaming: false, isSynchronizing: false, content: finalText,
        parts: [...toolParts.map((part) => part.type === "tool" && part.status === "started" ? { ...part, status: "completed" as const } : part), ...(fixtureState.sends === 2 ? [nestedSubagentParts, todoPart, artifactPart, ...longToolLabelParts] : []), { type: "text", content: finalText }],
      }));
    };
    fixtureState.fail = () => {
      setRunning(false);
      setSettling(false);
      updateActiveMessage((message) => ({ ...message, isStreaming: false, isSynchronizing: false,
        parts: [...(message.parts || []), { type: "run_status", event_id: "synthetic-failure", event_type: "run_failed", severity: "error", stage: "private-stage", message: "private-error-secret" }],
      }));
    };
    fixtureState.restoreHistory = () => {
      setMessages((current) => current.map((message) => ({ ...message, isStreaming: false })));
      setRunning(false);
      setHistoryRevision((revision) => revision + 1);
    };
    fixtureState.showGeneric = () => setGeneric(true);
    fixtureState.showQuestions = () => setQuestionProjection({ run_id: "run-synthetic-1", state: "open", inputs: [], questions: [questionBatch] });
    // Exceptional content keeps a reachable fallback: 8 options and a long question
    // must not promise the no-scroll behaviour that ordinary short questions have.
    fixtureState.showLongQuestion = () => setQuestionProjection({ run_id: "run-synthetic-1", state: "open", inputs: [], questions: [{
      ...questionBatch,
      questions: [{ key: "long", header: "长问题", multiSelect: false, question: "请选择处理方式：" + "这是一个用于验证长问题在受限高度内仍可完整读取的问题描述。".repeat(8),
        options: Array.from({ length: 8 }, (_, index) => ({ key: `opt-${index}`, label: `处理方式 ${index + 1}`, description: "选项说明用于填充真实高度" })) }],
    }] });
    fixtureState.attach = () => setAttachments([{ id: "attachment-synthetic", key: "attachment-synthetic", name: "已确认附件.pdf", type: "document", mimeType: "application/pdf", size: 2048 }]);
    // Multiple closed Runs must share one scroll budget, including their titles.
    fixtureState.showHistoryWithReceipts = () => setHistoryProjections(Array.from({ length: 10 }, (_, index) => longHistoryProjection(index)));
    fixtureState.clearHistory = () => setHistoryProjections([]);
    fixtureState.clearQuestion = () => setQuestionProjection(null);
    fixtureState.resolveQuestions = () => setQuestionProjection((previous) => previous ? { ...previous, questions: previous.questions.map((question) => ({ ...question, status: "resolved" as const })) } : previous);
    fixtureState.performanceStream = async (count) => {
      const started = performance.now();
      for (let index = 0; index < count; index += 1) {
        setMessages((current) => current.map((message) => message.id === "performance-assistant-499" ? { ...message, content: "固定流式公开更新。".repeat(index + 1), parts: [{ type: "text", content: "固定流式公开更新。".repeat(index + 1) }, ...performanceToolParts] } : message));
        await new Promise(requestAnimationFrame);
      }
      return performance.now() - started;
    };
  }, [updateActiveMessage]);
  useEffect(() => {
    if (performanceMode) performance.mark("chat-fixture-mounted");
  }, [performanceMode]);
  return <div style={{ height: "100dvh", display: "flex", overflow: "hidden" }}>
    {performanceMode ? <span data-performance-ready aria-hidden="true" /> : null}
    <ChatView key={historyRevision} messages={messages} sessionId={performanceMode ? null : "session-synthetic"} conversationIdentityKey="synthetic-chat" currentRunId={running ? `run-synthetic-${fixtureState.sends}` : null}
      canStopGeneration={running && !settling} isLoading={running} isLoadingHistory={false} canSendMessage
      agentEmptyProfile={generic ? undefined : { agent_id: "agt_fixture", expected_revision: 1, name: "文件整理专家", description: "整理授权文件并汇总结果。", starter_prompts: ["请整理这批文件"], avatar_ref: "builtin:assistant", avatar_seed: "fixture", market_tags: [], completed_tasks: 0, is_favorite: false, published_at: "2026-01-01T00:00:00Z" }}
      tools={[]} onToggleTool={noop} onToggleCategory={noop} onToggleAll={noop} enabledToolsCount={0} totalToolsCount={0}
      skills={[]} taskSkills={[]} selectedSkillState={createSelectedSkillTaskState()} onSelectSkill={noop} onClearSelectedSkill={noop}
      onSelectedSkillRecoverable={ready} onSelectedSkillFilesReady={noop} skillsLoading={false} enabledSkillsCount={0} totalSkillsCount={0} enableSkills={false}
      agentOptions={{}} agentOptionValues={{}} onToggleAgentOption={noop} availableModels={generic ? [{ id: "model-a", value: "model-a", label: "合成模型甲" }, { id: "model-b", value: "model-b", label: "合成模型乙" }] : []} currentModelId={model} onSelectModel={setModel}
      onSendMessage={async (content, _options, sentAttachments) => {
        fixtureState.sends += 1;
        const turn = fixtureState.sends;
        setQuestionProjection(null);
        setMessages((current) => [...current,
          { id: `user-synthetic-${turn}`, runId: `run-synthetic-${turn}`, role: "user", content, attachments: sentAttachments, timestamp: new Date(turn * 1000) },
          { id: `assistant-synthetic-${turn}`, runId: `run-synthetic-${turn}`, role: "assistant", content: "", timestamp: new Date(turn * 1000 + 1), isStreaming: true, parts: [...toolParts, ...(turn === 2 ? [nestedSubagentParts, todoPart, artifactPart] : [])] },
        ]);
        setSettling(false);
        setRunning(true);
        return { status: "accepted" };
      }}
      canRetryPendingSubmission={false} onRetryPendingSubmission={ready}
      onStopGeneration={async () => {
        fixtureState.stops += 1;
        setRunning(false);
        updateActiveMessage((message) => ({ ...message, isStreaming: false, isSynchronizing: false, cancelled: true,
          parts: [...(message.parts || []).map((part) => part.type === "tool" && part.status === "started" ? { ...part, cancelled: true } : part), { type: "cancelled" }],
        }));
        return "acknowledged";
      }}
      onReconnect={ready} onLoadHistory={ready} runInputs={performanceMode || (!questionProjection && historyProjections.length === 0) ? undefined : runInputs} attachments={attachments} onAttachmentsChange={setAttachments} uploadControls={uploadControls}
      WorkbenchShellComponent={WorkbenchShell} />
  </div>;
}

function AuthenticatedFixture() {
  const { user, isLoading } = useAuth();
  return user && !isLoading ? <Fixture /> : null;
}

createRoot(document.getElementById("root")!).render(
  <BrowserRouter><AuthProvider><ModelCatalogProvider><ThemeProvider><LobeUiProvider>
    <AuthenticatedFixture /><ToastHost />
  </LobeUiProvider></ThemeProvider></ModelCatalogProvider></AuthProvider></BrowserRouter>,
);
