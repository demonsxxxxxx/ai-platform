import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeft, Bot, RefreshCw, Send } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";

import { useAgent } from "../../hooks/useAgent";
import { agentProfileApi } from "../../services/api/agentProfile";
import type { AgentProfilePublicProjection } from "../../types";
import { APP_ROUTE_PATHS } from "../../appRouteManifest";
import { marketProfileRequest, selectPublishedMarketProfile } from "./agentMarketSelection";

type CatalogState =
  | { phase: "loading"; profiles: readonly AgentProfilePublicProjection[]; error: null }
  | { phase: "ready"; profiles: readonly AgentProfilePublicProjection[]; error: null }
  | { phase: "error"; profiles: readonly AgentProfilePublicProjection[]; error: string };

function profileChatPath(profile: AgentProfilePublicProjection): string {
  return APP_ROUTE_PATHS.agentMarketChat
    .replace(":agentId", encodeURIComponent(profile.agent_id))
    .replace(":revision", String(profile.expected_revision));
}

/** Ordinary-user published-profile catalog and its revision-bound Chat entry. */
export function AgentMarketRoute() {
  const navigate = useNavigate();
  const { agentId, revision } = useParams<{ agentId?: string; revision?: string }>();
  const [catalog, setCatalog] = useState<CatalogState>({
    phase: "loading",
    profiles: [],
    error: null,
  });
  const [requestRevision, setRequestRevision] = useState(0);
  const activeProfile = useMemo(
    () => selectPublishedMarketProfile(catalog.profiles, agentId, revision),
    [agentId, catalog.profiles, revision],
  );

  useEffect(() => {
    let active = true;
    setCatalog((current) => ({ ...current, phase: "loading", error: null }));
    void agentProfileApi
      .listPublished()
      .then((response) => {
        if (active) {
          setCatalog({ phase: "ready", profiles: response.agent_profiles, error: null });
        }
      })
      .catch((error: unknown) => {
        if (active) {
          setCatalog({
            phase: "error",
            profiles: [],
            error: error instanceof Error ? error.message : "暂时无法加载已发布的智能体。",
          });
        }
      });
    return () => {
      active = false;
    };
  }, [requestRevision]);

  const refresh = useCallback(() => {
    setRequestRevision((current) => current + 1);
  }, []);

  if (agentId || revision) {
    return (
      <AgentMarketChat
        catalog={catalog}
        profile={activeProfile}
        onBack={() => navigate(APP_ROUTE_PATHS.agentMarket)}
        onRefresh={refresh}
      />
    );
  }

  return (
    <main data-agent-market className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 px-4 py-8 text-[var(--theme-text)] sm:px-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-[var(--theme-primary)]">智能体市场</p>
          <h1 className="mt-1 text-2xl font-semibold">选择已发布的智能体</h1>
          <p className="mt-2 max-w-2xl text-sm text-[var(--theme-text-secondary)]">
            仅展示当前租户中已发布且可用的智能体。配置与能力版本由平台在开始对话时校验。
          </p>
        </div>
        <button className="btn-secondary inline-flex items-center gap-2" onClick={refresh} type="button">
          <RefreshCw size={16} className={catalog.phase === "loading" ? "animate-spin" : undefined} aria-hidden="true" />
          刷新
        </button>
      </header>

      {catalog.phase === "error" ? (
        <section className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-800 dark:border-red-900/70 dark:bg-red-950/40 dark:text-red-200">
          <p>{catalog.error}</p>
          <button className="btn-secondary mt-3" onClick={refresh} type="button">重新加载</button>
        </section>
      ) : catalog.phase === "loading" ? (
        <p className="text-sm text-[var(--theme-text-secondary)]">正在加载已发布的智能体…</p>
      ) : catalog.profiles.length === 0 ? (
        <section className="rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-6 text-sm text-[var(--theme-text-secondary)]">
          当前没有可用的已发布智能体，请联系管理员发布后再试。
        </section>
      ) : (
        <section aria-label="已发布智能体" className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {catalog.profiles.map((profile) => (
            <button
              key={`${profile.agent_id}:${profile.expected_revision}`}
              className="flex min-h-44 flex-col rounded-xl border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-5 text-left transition-colors hover:border-[var(--theme-primary)] focus:outline-none focus:ring-2 focus:ring-[var(--theme-primary)]"
              onClick={() => navigate(profileChatPath(profile))}
              type="button"
            >
              <Bot size={22} className="text-[var(--theme-primary)]" aria-hidden="true" />
              <h2 className="mt-4 text-base font-semibold">{profile.name}</h2>
              <p className="mt-2 line-clamp-3 text-sm text-[var(--theme-text-secondary)]">
                {profile.description || "该智能体已通过平台发布。"}
              </p>
              <span className="mt-auto pt-4 text-sm font-medium text-[var(--theme-primary)]">开始对话</span>
            </button>
          ))}
        </section>
      )}
    </main>
  );
}

function AgentMarketChat({
  catalog,
  profile,
  onBack,
  onRefresh,
}: {
  catalog: CatalogState;
  profile: AgentProfilePublicProjection | null;
  onBack: () => void;
  onRefresh: () => void;
}) {
  const navigate = useNavigate();
  const [message, setMessage] = useState("");
  const [submitError, setSubmitError] = useState<string | null>(null);
  const chat = useAgent(useMemo(() => ({ getDisabledMcpTools: () => [] }), []));

  useEffect(() => {
    if (!chat.sessionId || !chat.currentRunId) return;
    navigate(
      APP_ROUTE_PATHS.chat.replace(":sessionId?", encodeURIComponent(chat.sessionId)),
      { replace: true },
    );
  }, [chat.currentRunId, chat.sessionId, navigate]);

  const submit = useCallback(async () => {
    if (!profile || !message.trim() || chat.isLoading) return;
    setSubmitError(null);
    const outcome = await chat.sendMessage(
      message.trim(),
      {},
      undefined,
      null,
      marketProfileRequest(profile),
    );
    if (outcome.status !== "accepted") {
      setSubmitError("无法启动对话。该智能体可能已更新、取消发布或您已无权使用；请返回市场刷新后重试。");
    }
  }, [chat, message, profile]);

  const unavailable = catalog.phase === "ready" && profile === null;
  return (
    <main data-agent-market-chat className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-6 px-4 py-8 text-[var(--theme-text)] sm:px-6">
      <button className="inline-flex w-fit items-center gap-2 text-sm text-[var(--theme-primary)] hover:underline" onClick={onBack} type="button">
        <ArrowLeft size={16} aria-hidden="true" /> 返回智能体市场
      </button>
      {catalog.phase === "loading" ? (
        <p className="text-sm text-[var(--theme-text-secondary)]">正在确认智能体发布状态…</p>
      ) : catalog.phase === "error" ? (
        <section className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-800 dark:border-red-900/70 dark:bg-red-950/40 dark:text-red-200">
          <p>{catalog.error}</p>
          <button className="btn-secondary mt-3" onClick={onRefresh} type="button">重新加载</button>
        </section>
      ) : unavailable ? (
        <section className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-900/70 dark:bg-amber-950/40 dark:text-amber-100">
          <p>该智能体已更新、取消发布或您没有使用权限。为保护版本绑定，本次不会降级到其他智能体。</p>
          <button className="btn-secondary mt-3" onClick={onBack} type="button">返回市场</button>
        </section>
      ) : profile ? (
        <section className="rounded-xl border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-6">
          <p className="text-sm font-medium text-[var(--theme-primary)]">专属对话</p>
          <h1 className="mt-1 text-2xl font-semibold">{profile.name}</h1>
          {profile.description ? <p className="mt-2 text-sm text-[var(--theme-text-secondary)]">{profile.description}</p> : null}
          <p className="mt-4 text-xs text-[var(--theme-text-secondary)]">开始后将严格绑定当前已发布版本；版本变更或权限失效会安全拒绝。</p>
          <label className="mt-6 flex flex-col gap-2">
            <span className="text-sm font-medium">你的消息</span>
            <textarea
              aria-label="发送给已发布智能体的消息"
              className="min-h-32 w-full resize-y rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-canvas)] px-3 py-2 text-sm outline-none focus:border-[var(--theme-primary)]"
              onChange={(event) => setMessage(event.target.value)}
              placeholder="输入消息后开始对话"
              value={message}
            />
          </label>
          {submitError ? <p className="mt-3 text-sm text-red-700 dark:text-red-300">{submitError}</p> : null}
          <div className="mt-4 flex justify-end">
            <button className="btn-primary inline-flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-60" disabled={!message.trim() || chat.isLoading} onClick={() => void submit()} type="button">
              <Send size={16} aria-hidden="true" /> {chat.isLoading ? "正在启动…" : "开始对话"}
            </button>
          </div>
        </section>
      ) : null}
    </main>
  );
}
