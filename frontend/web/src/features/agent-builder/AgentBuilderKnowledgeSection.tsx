import { BookOpen, CircleAlert, Database, Loader2, Search, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AgentBuilderDialog } from "../../components/agent-builder/AgentBuilderDialog";
import type {
  KnowledgeBuilderCatalog,
  KnowledgeBuilderSource,
  KnowledgeRetrievalProfile,
} from "../../types";
import type { AgentBuilderEditor } from "./agentBuilderAdapter";

const MAX_KNOWLEDGE_SOURCES = 8;

interface AgentBuilderKnowledgeSectionProps {
  disabled: boolean;
  editor: AgentBuilderEditor;
  knowledgeResolved: boolean;
  retrievalProfiles: readonly KnowledgeRetrievalProfile[];
  sources: readonly KnowledgeBuilderSource[];
  loadKnowledgeSources: (params?: {
    cursor?: string | null;
    q?: string;
    selectedSourceIds?: readonly string[];
    replace?: boolean;
  }) => Promise<KnowledgeBuilderCatalog | undefined>;
  onChange: (
    patch: Pick<AgentBuilderEditor, "knowledgeSourceIds" | "retrievalProfileId">,
  ) => void;
}

/** Governed Knowledge binding editor; it retains stale server pins until explicit removal. */
export function AgentBuilderKnowledgeSection({
  disabled,
  editor,
  knowledgeResolved,
  retrievalProfiles,
  sources,
  loadKnowledgeSources,
  onChange,
}: AgentBuilderKnowledgeSectionProps) {
  const [dialogOpen, setDialogOpen] = useState(false);
  const [expanded, setExpanded] = useState(editor.knowledgeSourceIds.length > 0);
  const [sourceQuery, setSourceQuery] = useState("");
  const [resultIds, setResultIds] = useState<string[] | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const searchEpoch = useRef(0);
  const sourcesById = useMemo(
    () => new Map(sources.map((source) => [source.id, source])),
    [sources],
  );
  const unavailableSourceIds = knowledgeResolved
    ? editor.knowledgeSourceIds.filter((sourceId) => !sourcesById.has(sourceId))
    : [];
  const activeRetrievalProfiles = retrievalProfiles.filter(
    (profile) => profile.status === "active",
  );
  const selectedRetrievalProfile = activeRetrievalProfiles.find(
    (profile) => profile.id === editor.retrievalProfileId,
  );
  const availableSources = useMemo(
    () => sources.filter((source) => source.available !== false),
    [sources],
  );
  const displayedSources = useMemo(() => {
    if (resultIds === null) return availableSources;
    return resultIds
      .map((sourceId) => sourcesById.get(sourceId))
      .filter((source): source is KnowledgeBuilderSource => Boolean(source?.available));
  }, [availableSources, resultIds, sourcesById]);

  const loadSourcePage = useCallback(async (cursor: string | null, append: boolean) => {
    const epoch = ++searchEpoch.current;
    setSearchLoading(true);
    setSearchError(null);
    const page = await loadKnowledgeSources({
      cursor,
      q: sourceQuery.trim(),
      selectedSourceIds: editor.knowledgeSourceIds,
    });
    if (epoch !== searchEpoch.current) return;
    if (!page) {
      setSearchError("知识源搜索失败，请重试。");
      setSearchLoading(false);
      return;
    }
    const pageIds = page.sources
      .filter((source) => source.available)
      .map((source) => source.id);
    setResultIds((current) =>
      append ? [...new Set([...(current ?? []), ...pageIds])] : pageIds,
    );
    setNextCursor(page.next_cursor);
    setSearchLoading(false);
  }, [editor.knowledgeSourceIds, loadKnowledgeSources, sourceQuery]);

  useEffect(() => {
    if (!dialogOpen || !knowledgeResolved) return;
    const timer = window.setTimeout(() => {
      void loadSourcePage(null, false);
    }, 180);
    return () => {
      window.clearTimeout(timer);
      searchEpoch.current += 1;
    };
  }, [dialogOpen, knowledgeResolved, loadSourcePage]);

  const toggleSource = (sourceId: string) => {
    const selected = editor.knowledgeSourceIds.includes(sourceId);
    const knowledgeSourceIds = selected
      ? editor.knowledgeSourceIds.filter((selectedId) => selectedId !== sourceId)
      : [...editor.knowledgeSourceIds, sourceId];
    if (!selected && knowledgeSourceIds.length > MAX_KNOWLEDGE_SOURCES) return;
    onChange({
      knowledgeSourceIds,
      retrievalProfileId: knowledgeSourceIds.length === 0
        ? null
        : editor.retrievalProfileId ?? activeRetrievalProfiles[0]?.id ?? null,
    });
  };

  const removeSource = (sourceId: string) => {
    const knowledgeSourceIds = editor.knowledgeSourceIds.filter(
      (selectedId) => selectedId !== sourceId,
    );
    onChange({
      knowledgeSourceIds,
      retrievalProfileId: knowledgeSourceIds.length === 0
        ? null
        : editor.retrievalProfileId,
    });
  };

  return (
    <>
      <section aria-labelledby="agent-knowledge-heading">
        <details
          className="rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-4"
          data-agent-builder-knowledge-settings
          onToggle={(event) => setExpanded(event.currentTarget.open)}
          open={expanded}
        >
          <summary className="cursor-pointer text-sm font-medium">
            企业知识库（可选） · 已选择 {editor.knowledgeSourceIds.length} 项
          </summary>
          <p className="mt-3 text-sm leading-6 text-[var(--theme-text-secondary)]">
            将已治理的 RAGFlow 知识源绑定到专家。发布和每次运行都会由服务端重新校验状态与访问范围。
          </p>
          <div className="mb-4 mt-4 flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <BookOpen
                aria-hidden="true"
                className="text-[var(--theme-text-secondary)]"
                size={17}
              />
              <h3 id="agent-knowledge-heading" className="text-sm font-semibold">
                知识源
              </h3>
            </div>
            <button
              className="btn-secondary inline-flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={disabled}
              onClick={() => setDialogOpen(true)}
              type="button"
            >
              <Database aria-hidden="true" size={15} />
              配置知识库
            </button>
          </div>

          {editor.knowledgeSourceIds.length === 0 ? (
            <p className="text-sm text-[var(--theme-text-secondary)]">
              未绑定知识源；该专家仍可作为普通 Skills Agent 保存和发布。
            </p>
          ) : (
            <div className="divide-y divide-[var(--theme-border)] border-y border-[var(--theme-border)]">
              {editor.knowledgeSourceIds.map((sourceId) => {
                const source = sourcesById.get(sourceId);
                const unavailable = knowledgeResolved && (!source || !source.available);
                return (
                  <div key={sourceId} className="flex items-start gap-3 py-3">
                    <span
                      aria-hidden="true"
                      className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${
                        !knowledgeResolved
                          ? "bg-[var(--theme-border-strong)]"
                          : source
                            ? "bg-[var(--theme-success)]"
                            : "bg-[var(--theme-danger)]"
                      }`}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block break-words text-sm font-medium">
                        {source?.name ?? sourceId}
                      </span>
                      <span
                        className={`mt-1 block text-xs ${
                          unavailable
                            ? "text-[var(--theme-danger)]"
                            : "text-[var(--theme-text-secondary)]"
                        }`}
                      >
                        {!knowledgeResolved
                          ? "知识目录尚未完整加载，已保留服务端绑定。"
                          : source
                            ? source.available
                              ? `${source.connection_name} · ${
                                  source.visibility === "enterprise"
                                    ? "全公司可用"
                                    : `限定 ${source.allowed_department_count} 个部门`
                                } · 权限版本 ${source.authorization_version}`
                              : "知识源或连接当前不可用，已保留绑定并阻止发布。"
                            : "当前目录中不可用，已保留绑定并阻止发布。"}
                      </span>
                    </span>
                    <button
                      aria-label={`移除知识源 ${source?.name ?? sourceId}`}
                      className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-md text-[var(--theme-text-secondary)] hover:bg-[var(--theme-hover)] hover:text-[var(--theme-text)] disabled:cursor-not-allowed disabled:opacity-60"
                      disabled={disabled}
                      onClick={() => removeSource(sourceId)}
                      type="button"
                    >
                      <X aria-hidden="true" size={16} />
                    </button>
                  </div>
                );
              })}
            </div>
          )}

          {editor.knowledgeSourceIds.length > 0 ? (
            <div className="mt-4 rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-canvas)] p-3 text-sm">
              <span className="text-[var(--theme-text-secondary)]">检索策略</span>
              <span className="ml-2 font-medium">
                {selectedRetrievalProfile?.name ?? editor.retrievalProfileId ?? "尚未选择"}
              </span>
            </div>
          ) : null}

          {unavailableSourceIds.length > 0 ? (
            <p className="mt-3 flex items-start gap-2 text-sm text-[var(--theme-danger)]" role="alert">
              <CircleAlert aria-hidden="true" className="mt-0.5 shrink-0" size={16} />
              <span>{unavailableSourceIds.length} 项知识源不可用，请明确移除或重新选择。</span>
            </p>
          ) : null}
        </details>
      </section>

      <AgentBuilderDialog
        isOpen={dialogOpen}
        onClose={() => setDialogOpen(false)}
        title="配置企业知识库"
      >
        {!knowledgeResolved ? (
          <p className="text-sm text-[var(--theme-text-secondary)]">
            知识目录尚未完整加载。已保存的绑定不会被自动移除。
          </p>
        ) : availableSources.length === 0 && !sourceQuery ? (
          <p className="text-sm text-[var(--theme-text-secondary)]">
            当前没有已启用且连接健康的知识源，请先在知识库管理中完成同步与授权。
          </p>
        ) : (
          <div className="space-y-5">
            <fieldset>
              <legend className="text-sm font-semibold">
                选择知识源（最多 {MAX_KNOWLEDGE_SOURCES} 项）
              </legend>
              <p className="mt-1 text-xs text-[var(--theme-text-secondary)]">
                已选择 {editor.knowledgeSourceIds.length}/{MAX_KNOWLEDGE_SOURCES}
              </p>
              <label className="relative mt-3 block">
                <span className="sr-only">搜索知识源</span>
                <Search
                  aria-hidden="true"
                  className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--theme-text-tertiary)]"
                  size={16}
                />
                <input
                  className="input-field w-full pl-9"
                  onChange={(event) => setSourceQuery(event.target.value)}
                  placeholder="按知识源、连接或说明搜索"
                  value={sourceQuery}
                />
              </label>
              {searchError ? (
                <div
                  className="mt-3 flex items-center justify-between gap-3 rounded-md bg-red-500/10 p-3 text-sm text-[var(--theme-danger)]"
                  role="alert"
                >
                  <span>{searchError}</span>
                  <button
                    className="btn-secondary"
                    onClick={() => void loadSourcePage(null, false)}
                    type="button"
                  >
                    重试
                  </button>
                </div>
              ) : null}
              <div className="mt-3 divide-y divide-[var(--theme-border)] border-y border-[var(--theme-border)]">
                {displayedSources.map((source) => {
                  const selected = editor.knowledgeSourceIds.includes(source.id);
                  return (
                    <label
                      key={source.id}
                      className="flex cursor-pointer items-start gap-3 px-1 py-3 hover:bg-[var(--theme-hover)]"
                    >
                      <input
                        checked={selected}
                        disabled={
                          disabled ||
                          (!selected && editor.knowledgeSourceIds.length >= MAX_KNOWLEDGE_SOURCES)
                        }
                        onChange={() => toggleSource(source.id)}
                        type="checkbox"
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block font-medium">{source.name}</span>
                        <span className="mt-1 block text-sm text-[var(--theme-text-secondary)]">
                          {source.description || source.connection_name}
                        </span>
                      </span>
                      <span className="shrink-0 text-xs text-[var(--theme-text-secondary)]">
                        v{source.authorization_version}
                      </span>
                    </label>
                  );
                })}
                {!searchLoading && displayedSources.length === 0 ? (
                  <p className="px-1 py-5 text-sm text-[var(--theme-text-secondary)]">
                    没有匹配的可用知识源。
                  </p>
                ) : null}
              </div>
              {searchLoading ? (
                <p className="mt-3 flex items-center gap-2 text-sm text-[var(--theme-text-secondary)]" role="status">
                  <Loader2 aria-hidden="true" className="animate-spin" size={15} />
                  正在加载知识源…
                </p>
              ) : nextCursor ? (
                <button
                  className="btn-secondary mt-3 w-full"
                  onClick={() => void loadSourcePage(nextCursor, true)}
                  type="button"
                >
                  加载更多
                </button>
              ) : null}
            </fieldset>

            {editor.knowledgeSourceIds.length > 0 ? (
              <fieldset>
                <legend className="text-sm font-semibold">检索策略</legend>
                {activeRetrievalProfiles.length === 0 ? (
                  <p className="mt-2 text-sm text-[var(--theme-danger)]" role="alert">
                    当前没有可用的检索策略，不能保存知识库配置。
                  </p>
                ) : (
                  <div className="mt-2 space-y-2">
                    {activeRetrievalProfiles.map((profile) => (
                      <label
                        key={`${profile.id}:${profile.revision}`}
                        className="flex cursor-pointer items-start gap-3 rounded-md border border-[var(--theme-border)] p-3 hover:bg-[var(--theme-hover)]"
                      >
                        <input
                          checked={editor.retrievalProfileId === profile.id}
                          disabled={disabled}
                          name="agent-retrieval-profile"
                          onChange={() =>
                            onChange({
                              knowledgeSourceIds: editor.knowledgeSourceIds,
                              retrievalProfileId: profile.id,
                            })}
                          type="radio"
                        />
                        <span className="min-w-0">
                          <span className="block font-medium">{profile.name}</span>
                          <span className="mt-1 block text-sm text-[var(--theme-text-secondary)]">
                            {profile.description} · revision {profile.revision}
                          </span>
                        </span>
                      </label>
                    ))}
                  </div>
                )}
              </fieldset>
            ) : null}
          </div>
        )}
      </AgentBuilderDialog>
    </>
  );
}
