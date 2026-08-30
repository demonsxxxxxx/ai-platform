import { Database, Loader2, Plus, RefreshCw, Search } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import toast from "react-hot-toast";

import {
  CatalogPager,
  ModalShell,
} from "../../features/knowledge/components/CatalogPrimitives";
import {
  ConnectionForm,
  CredentialRotateForm,
} from "../../features/knowledge/components/ConnectionForms";
import {
  ConnectionCatalog,
  SourceCatalog,
} from "../../features/knowledge/components/KnowledgeCatalogs";
import {
  SourceAclForm,
  SourceDetailsForm,
} from "../../features/knowledge/components/SourceForms";
import { knowledgeApi } from "../../services/api";
import type {
  KnowledgeConnection,
  KnowledgeSource,
} from "../../types/knowledge";
import { PanelHeader } from "../common/PanelHeader";
import { workbenchSurface } from "../workbench/workbenchSurface";

type CatalogTab = "connections" | "sources";
type SourceStatusFilter = "" | "pending_review" | "active" | "disabled" | "missing";

export function KnowledgePanel() {
  const [tab, setTab] = useState<CatalogTab>("connections");
  const [query, setQuery] = useState("");
  const [sourceConnectionFilter, setSourceConnectionFilter] = useState("");
  const [sourceStatusFilter, setSourceStatusFilter] =
    useState<SourceStatusFilter>("");
  const [connectionCursors, setConnectionCursors] = useState<Array<string | null>>([
    null,
  ]);
  const [connectionPage, setConnectionPage] = useState(0);
  const [sourceCursors, setSourceCursors] = useState<Array<string | null>>([null]);
  const [sourcePage, setSourcePage] = useState(0);
  const [connections, setConnections] = useState<KnowledgeConnection[]>([]);
  const [connectionOptions, setConnectionOptions] = useState<KnowledgeConnection[]>([]);
  const [connectionOptionsLoading, setConnectionOptionsLoading] = useState(false);
  const [connectionOptionsError, setConnectionOptionsError] = useState<string | null>(null);
  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [nextConnectionCursor, setNextConnectionCursor] = useState<string | null>(null);
  const [nextSourceCursor, setNextSourceCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [rotateConnection, setRotateConnection] =
    useState<KnowledgeConnection | null>(null);
  const [aclSource, setAclSource] = useState<KnowledgeSource | null>(null);
  const [editSource, setEditSource] = useState<KnowledgeSource | null>(null);
  const requestEpoch = useRef(0);
  const connectionOptionsEpoch = useRef(0);

  const currentCursor =
    tab === "connections"
      ? connectionCursors[connectionPage]
      : sourceCursors[sourcePage];

  const resetPaging = useCallback(() => {
    setConnectionCursors([null]);
    setConnectionPage(0);
    setSourceCursors([null]);
    setSourcePage(0);
  }, []);

  const load = useCallback(async (fromFirstPage = false) => {
    const epoch = ++requestEpoch.current;
    setLoading(true);
    setError(null);
    try {
      if (tab === "connections") {
        const page = await knowledgeApi.listConnections({
          cursor: fromFirstPage ? null : currentCursor,
          q: query.trim(),
        });
        if (epoch !== requestEpoch.current) return;
        setConnections(page.items);
        setNextConnectionCursor(page.next_cursor);
      } else {
        const page = await knowledgeApi.listSources({
          cursor: fromFirstPage ? null : currentCursor,
          q: query.trim(),
          connectionId: sourceConnectionFilter || undefined,
          status: sourceStatusFilter || undefined,
        });
        if (epoch !== requestEpoch.current) return;
        setSources(page.items);
        setNextSourceCursor(page.next_cursor);
      }
    } catch (caught) {
      if (epoch !== requestEpoch.current) return;
      setError(caught instanceof Error ? caught.message : "知识库目录加载失败");
    } finally {
      if (epoch === requestEpoch.current) setLoading(false);
    }
  }, [
    currentCursor,
    query,
    sourceConnectionFilter,
    sourceStatusFilter,
    tab,
  ]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 180);
    return () => window.clearTimeout(timer);
  }, [load]);

  const loadConnectionOptions = useCallback(async () => {
    const epoch = ++connectionOptionsEpoch.current;
    setConnectionOptionsLoading(true);
    setConnectionOptionsError(null);
    try {
      const collected = new Map<string, KnowledgeConnection>();
      let cursor: string | null = null;
      for (let pageIndex = 0; pageIndex < 10; pageIndex += 1) {
        const page = await knowledgeApi.listConnections({ limit: 100, cursor });
        if (epoch !== connectionOptionsEpoch.current) return;
        page.items.forEach((connection) => collected.set(connection.id, connection));
        cursor = page.next_cursor;
        if (!cursor) {
          setConnectionOptions([...collected.values()]);
          return;
        }
      }
      setConnectionOptions([]);
      setConnectionOptionsError("连接数量超过筛选器的安全加载上限，请使用知识源搜索。");
    } catch {
      if (epoch !== connectionOptionsEpoch.current) return;
      setConnectionOptions([]);
      setConnectionOptionsError("连接筛选项加载失败。");
    } finally {
      if (epoch === connectionOptionsEpoch.current) setConnectionOptionsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (tab !== "sources") return;
    void loadConnectionOptions();
    return () => {
      connectionOptionsEpoch.current += 1;
    };
  }, [loadConnectionOptions, tab]);

  const mutate = useCallback(
    async (key: string, action: () => Promise<unknown>, success: string) => {
      setBusyKey(key);
      try {
        await action();
        toast.success(success);
        await load();
      } catch (caught) {
        toast.error(caught instanceof Error ? caught.message : "操作失败");
        // A failed check/sync can still commit a safe lifecycle status. Refresh
        // the card so the administrator sees that authoritative failure state.
        await load();
      } finally {
        setBusyKey(null);
      }
    },
    [load],
  );

  const activeOnPage = useMemo(
    () =>
      tab === "connections"
        ? connections.filter((item) => item.status === "active").length
        : sources.filter((item) => item.status === "active").length,
    [connections, sources, tab],
  );

  const reloadAfterModal = useCallback(
    async (message: string) => {
      resetPaging();
      await load(true);
      toast.success(message);
    },
    [load, resetPaging],
  );

  return (
    <div className={workbenchSurface.page} data-knowledge-panel>
      <PanelHeader
        title="知识库"
        subtitle="连接公司 RAGFlow，将已解析的数据集治理为可授权给智能体的知识源。"
        icon={<Database />}
        actions={
          tab === "connections" ? (
            <button
              className="btn-primary"
              type="button"
              onClick={() => setCreateOpen(true)}
            >
              <Plus size={16} />
              新建连接
            </button>
          ) : undefined
        }
      >
        <div
          className="mt-3 flex flex-wrap items-center gap-2"
          role="tablist"
          aria-label="知识库管理"
        >
          {(["connections", "sources"] as const).map((item) => (
            <button
              key={item}
              type="button"
              role="tab"
              aria-selected={tab === item}
              className={tab === item ? "btn-primary" : "btn-secondary"}
              onClick={() => {
                setTab(item);
                setQuery("");
                resetPaging();
              }}
            >
              {item === "connections" ? "RAGFlow 连接" : "知识源与部门权限"}
            </button>
          ))}
        </div>
      </PanelHeader>

      <div className={workbenchSurface.catalog.toolbar}>
        <div className={workbenchSurface.catalog.toolbarShell}>
          <div className={workbenchSurface.catalog.toolbarRow}>
            <label className="relative min-w-0 flex-1">
              <Search
                size={17}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--theme-text-tertiary)]"
              />
              <span className="sr-only">搜索知识库目录</span>
              <input
                className="panel-search h-10 pl-9"
                value={query}
                onChange={(event) => {
                  setQuery(event.target.value);
                  resetPaging();
                }}
                placeholder={tab === "connections" ? "搜索连接名称" : "搜索知识源名称"}
              />
            </label>
            <span className="text-xs text-[var(--theme-text-secondary)]">
              本页 {activeOnPage} 个{tab === "connections" ? "可用连接" : "已启用知识源"}
            </span>
            <button
              className="btn-icon"
              onClick={() => void load()}
              type="button"
              aria-label="刷新目录"
            >
              <RefreshCw size={17} className={loading ? "animate-spin" : ""} />
            </button>
          </div>
          {tab === "sources" ? (
            <div className="mt-3 flex flex-wrap gap-2 border-t border-[var(--theme-border)] pt-3">
              <label className="text-xs text-[var(--theme-text-secondary)]">
                <span className="sr-only">按连接筛选</span>
                <select
                  className="input-field min-w-44"
                  disabled={connectionOptionsLoading || Boolean(connectionOptionsError)}
                  value={sourceConnectionFilter}
                  onChange={(event) => {
                    setSourceConnectionFilter(event.target.value);
                    resetPaging();
                  }}
                >
                  <option value="">全部连接</option>
                  {connectionOptions.map((connection) => (
                    <option key={connection.id} value={connection.id}>
                      {connection.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="text-xs text-[var(--theme-text-secondary)]">
                <span className="sr-only">按状态筛选</span>
                <select
                  className="input-field min-w-36"
                  value={sourceStatusFilter}
                  onChange={(event) => {
                    setSourceStatusFilter(event.target.value as SourceStatusFilter);
                    resetPaging();
                  }}
                >
                  <option value="">全部状态</option>
                  <option value="pending_review">待审核</option>
                  <option value="active">可用</option>
                  <option value="disabled">已停用</option>
                  <option value="missing">上游已缺失</option>
                </select>
              </label>
              {connectionOptionsError ? (
                <div
                  className="flex items-center gap-2 text-xs text-[var(--theme-danger)]"
                  role="alert"
                >
                  <span>{connectionOptionsError}</span>
                  <button
                    className="btn-secondary"
                    onClick={() => void loadConnectionOptions()}
                    type="button"
                  >
                    重试
                  </button>
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>

      <div className={workbenchSurface.catalog.content}>
        {error ? (
          <div
            role="alert"
            className="rounded-lg border border-red-300/40 bg-red-500/10 p-4 text-sm text-red-700 dark:text-red-300"
          >
            {error}
          </div>
        ) : loading ? (
          <div
            className="flex min-h-64 items-center justify-center"
            role="status"
            aria-live="polite"
          >
            <Loader2 className="animate-spin text-[var(--theme-text-secondary)]" />
            <span className="sr-only">正在加载知识库目录</span>
          </div>
        ) : tab === "connections" ? (
          <ConnectionCatalog
            connections={connections}
            busyKey={busyKey}
            mutate={mutate}
            onRotateCredential={setRotateConnection}
          />
        ) : (
          <SourceCatalog
            sources={sources}
            busyKey={busyKey}
            mutate={mutate}
            onEdit={setEditSource}
            onEditAcl={setAclSource}
          />
        )}
      </div>

      {!loading && !error ? (
        tab === "connections" ? (
          <CatalogPager
            page={connectionPage}
            hasNext={Boolean(nextConnectionCursor)}
            onPrevious={() => setConnectionPage((value) => Math.max(0, value - 1))}
            onNext={() => {
              if (!nextConnectionCursor) return;
              setConnectionCursors((values) => [
                ...values.slice(0, connectionPage + 1),
                nextConnectionCursor,
              ]);
              setConnectionPage((value) => value + 1);
            }}
          />
        ) : (
          <CatalogPager
            page={sourcePage}
            hasNext={Boolean(nextSourceCursor)}
            onPrevious={() => setSourcePage((value) => Math.max(0, value - 1))}
            onNext={() => {
              if (!nextSourceCursor) return;
              setSourceCursors((values) => [
                ...values.slice(0, sourcePage + 1),
                nextSourceCursor,
              ]);
              setSourcePage((value) => value + 1);
            }}
          />
        )
      ) : null}

      {createOpen ? (
        <ModalShell title="新建 RAGFlow 连接" onClose={() => setCreateOpen(false)}>
          <ConnectionForm
            onClose={() => setCreateOpen(false)}
            onCreated={async () => {
              setCreateOpen(false);
              await reloadAfterModal("RAGFlow 连接已保存");
            }}
          />
        </ModalShell>
      ) : null}
      {rotateConnection ? (
        <ModalShell title="更新 RAGFlow 凭据" onClose={() => setRotateConnection(null)}>
          <CredentialRotateForm
            connection={rotateConnection}
            onClose={() => setRotateConnection(null)}
            onSaved={async () => {
              setRotateConnection(null);
              await reloadAfterModal("新凭据已保存，请检查并激活");
            }}
          />
        </ModalShell>
      ) : null}
      {editSource ? (
        <ModalShell title="编辑知识源信息" onClose={() => setEditSource(null)}>
          <SourceDetailsForm
            source={editSource}
            onClose={() => setEditSource(null)}
            onSaved={async () => {
              setEditSource(null);
              await reloadAfterModal("知识源信息已更新");
            }}
          />
        </ModalShell>
      ) : null}
      {aclSource ? (
        <ModalShell title="配置知识源部门权限" onClose={() => setAclSource(null)}>
          <SourceAclForm
            source={aclSource}
            onClose={() => setAclSource(null)}
            onSaved={async () => {
              setAclSource(null);
              await reloadAfterModal("知识源权限已更新");
            }}
          />
        </ModalShell>
      ) : null}
    </div>
  );
}
