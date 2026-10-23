import { Button, TextArea } from "@lobehub/ui/base-ui";
import { useLayoutEffect, useRef, useState, type FormEvent } from "react";
import { BookOpen, Loader2, MessageCircle, Send, ShieldCheck, Sparkles, Trash2 } from "lucide-react";
import type { Message } from "../../types";
import { useAuth } from "../../hooks/useAuth";
import { getAccessToken } from "../../services/api/token";
import { getSopRagflowConfig, streamSopAnswer, type SopRagflowConfig } from "./sopTransport";
import { AppFrame, AgentStatus, ConnectionNotice } from "./InternalApplicationFrame";

const SOP_SUGGESTIONS = [
  "员工报销申请单在 OA 系统如何发起？",
  "费用报销需要提交哪些发票和附件？",
  "询比价采购流程包含哪些步骤？",
  "供应商准入流程是什么？",
];

function formatTime(value: Date): string {
  return value.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function MessageBubble({ message }: { message: Message }) {
  const user = message.role === "user";
  return (
    <div className={`flex gap-3 ${user ? "flex-row-reverse" : ""}`}>
      <span className={`flex size-8 shrink-0 items-center justify-center rounded-full ${user ? "bg-[var(--theme-bg-sidebar)] text-[var(--theme-primary)]" : "bg-[var(--theme-bg-sidebar)] text-[var(--theme-primary)]"}`}>
        {user ? <MessageCircle size={16} /> : <BookOpen size={16} />}
      </span>
      <div className={`max-w-[min(760px,85%)] ${user ? "items-end" : "items-start"} flex flex-col gap-1`}>
        <span className="text-[11px] text-[var(--theme-text-secondary)]">{user ? "您" : "SOP 问询助手"}</span>
        <div className={`whitespace-pre-wrap rounded-lg px-4 py-3 text-sm leading-6 ${user ? "bg-[var(--theme-primary)] text-[var(--theme-primary-foreground)]" : "border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] text-[var(--theme-text)]"}`}>
          {message.content || "正在生成回答…"}
          {message.isStreaming && <Loader2 size={14} className="ml-2 inline animate-spin" />}
        </div>
        <span className="text-[11px] text-[var(--theme-text-secondary)]">{formatTime(message.timestamp)}</span>
      </div>
    </div>
  );
}

export function KnowledgeBaseApplication({ config = getSopRagflowConfig() }: { config?: SopRagflowConfig | null }) {
  const { user, isAuthenticated } = useAuth();
  const authMarker = getAccessToken();
  const scope = JSON.stringify([user?.tenant_id ?? "", user?.id ?? "", isAuthenticated, authMarker]);
  const generation = useRef(0);
  const requestRef = useRef<AbortController | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [sessionId, setSessionId] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [draft, setDraft] = useState("");
  const [localError, setLocalError] = useState("");
  useLayoutEffect(() => {
    generation.current += 1;
    setMessages([]);
    setSessionId("");
    setDraft("");
    setLocalError("");
    setIsLoading(false);
    return () => {
      generation.current += 1;
      requestRef.current?.abort();
      requestRef.current = null;
    };
  }, [scope]);
  const configured = config !== null;

  const clearMessages = () => {
    if (isLoading) return;
    setMessages([]);
    setSessionId("");
    setLocalError("");
  };

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    const text = draft.trim();
    if (!text || requestRef.current || !isAuthenticated || !authMarker || getAccessToken() !== authMarker) return;
    if (!config) {
      setLocalError("公司知识库访问配置未提供。");
      return;
    }

    const controller = new AbortController();
    requestRef.current = controller;
    const owner = generation.current;
    const ownsRequest = () => {
      const current = generation.current === owner && requestRef.current === controller &&
        isAuthenticated && getAccessToken() === authMarker && !controller.signal.aborted;
      if (!current) controller.abort();
      return current;
    };
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
        if (!ownsRequest()) return;
        setMessages((current) =>
          current.map((message) =>
            message.id === assistantId ? { ...message, content: answer } : message,
          ),
        );
      }, config, controller.signal);
      if (!ownsRequest()) return;
      setSessionId(result.sessionId);
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId
            ? { ...message, content: result.answer, isStreaming: false }
            : message,
        ),
      );
    } catch (error) {
      if (!ownsRequest()) return;
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
      if (ownsRequest()) {
        requestRef.current = null;
        setIsLoading(false);
      }
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
        message="公司知识库暂未接入，请联系管理员配置后使用。"
      />
      <div className="grid min-h-0 gap-5 lg:grid-cols-[minmax(0,1fr)_300px]">
        <section className="flex h-[calc(100dvh-150px)] min-h-[420px] flex-col overflow-hidden rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] shadow-[0_2px_8px_rgba(31,58,80,0.04)]">
          <div className="shrink-0 flex items-center justify-between border-b border-[var(--theme-border)] px-5 py-4">
            <div>
              <h2 className="text-sm font-bold text-[var(--theme-text)]">问询对话</h2>
              <p className="mt-1 text-xs text-[var(--theme-text-secondary)]">回答将基于已授权的公司知识库内容</p>
            </div>
            <Button type="text"
              htmlType="button"
              onClick={clearMessages}
              disabled={messages.length === 0 || isLoading}
              className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs text-[var(--theme-text-secondary)] hover:bg-[var(--theme-bg-sidebar)] disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Trash2 size={14} /> 清空
            </Button>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-6 sm:px-8">
            {messages.length === 0 ? (
              <div className="mx-auto flex max-w-[680px] flex-col items-center pt-12 text-center sm:pt-20">
                <span className="flex size-16 items-center justify-center rounded-full bg-[var(--theme-bg-sidebar)] text-[var(--theme-primary)]"><Sparkles size={28} /></span>
                <h2 className="mt-5 text-xl font-bold text-[var(--theme-text)]">您好，我是 SOP 问询助手</h2>
                <p className="mt-2 max-w-[520px] text-sm leading-6 text-[var(--theme-text-secondary)]">我已接入公司知识库，可以帮助您查询制度、审批流程、操作指引和规范要求。</p>
                <div className="mt-8 grid w-full gap-2 sm:grid-cols-2">
                  {SOP_SUGGESTIONS.map((suggestion) => (
                    <Button type="text"
                      key={suggestion}
                      htmlType="button"
                      onClick={() => setDraft(suggestion)}
                      icon={<MessageCircle size={15} />}
                      style={{ height: "auto", minHeight: 48, justifyContent: "flex-start", whiteSpace: "normal", textAlign: "left" }}
                      className="rounded-lg border border-[var(--theme-border)] px-3 text-xs leading-5"
                    >
                      {suggestion}
                    </Button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="mx-auto flex max-w-[840px] flex-col gap-5">
                {messages.map((message) => <MessageBubble key={message.id} message={message} />)}
              </div>
            )}
          </div>
          <form onSubmit={submit} className="shrink-0 border-t border-[var(--theme-border)] bg-[var(--theme-workbench-canvas)] p-4 sm:p-5">
            {localError && <p className="mb-2 text-xs text-[var(--theme-danger-text)]">{localError}</p>}
            <div className="flex items-end gap-2 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-2 focus-within:border-[var(--theme-primary)] focus-within:ring-2 focus-within:ring-[var(--theme-border)]">
              <TextArea
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
                aria-label="制度或流程问题"
                variant="borderless" className="min-w-0 flex-1"
                classNames={{ input: "min-h-10 px-2 py-1.5 text-sm" }}
              />
              <Button type="primary"
                htmlType="submit" size="large" loading={isLoading}
                icon={<Send size={17} />}
                disabled={!draft.trim() || isLoading}
                className="shrink-0"
                aria-label="发送问题"
                title="发送问题"
              />
            </div>
            <p className="mt-2 text-[11px] text-[var(--theme-text-secondary)]">Enter 发送，Shift + Enter 换行</p>
          </form>
        </section>
        <aside className="h-fit rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-5 shadow-[0_2px_8px_rgba(31,58,80,0.04)]">
          <div className="flex items-center gap-2 text-sm font-bold text-[var(--theme-text)]"><ShieldCheck size={17} className="text-[var(--theme-primary)]" /> 使用说明</div>
          <div className="mt-4 space-y-4 text-xs leading-5 text-[var(--theme-text-secondary)]">
            <p>请输入具体的制度、流程或审批问题，助手会返回答案和相关依据。</p>
            <p>回答仅使用您当前账号有权访问的知识库内容。</p>
            <div className="rounded-md bg-[var(--theme-bg-sidebar)] p-3 text-[var(--theme-text-secondary)]">当前绑定：公司 SOP 知识库</div>
          </div>
        </aside>
      </div>
    </AppFrame>
  );
}
