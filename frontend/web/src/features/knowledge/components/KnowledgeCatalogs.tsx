import {
  CheckCircle2,
  Database,
  KeyRound,
  Loader2,
  Pencil,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";

import { workbenchSurface } from "../../../components/workbench/workbenchSurface";
import { knowledgeApi } from "../../../services/api";
import type {
  KnowledgeConnection,
  KnowledgeSource,
} from "../../../types/knowledge";
import { safeDate } from "../catalogFormat";
import { StatusBadge } from "./CatalogPrimitives";

export type CatalogMutation = (
  key: string,
  action: () => Promise<unknown>,
  success: string,
) => Promise<void>;

export function ConnectionCatalog({
  connections,
  busyKey,
  mutate,
  onRotateCredential,
}: {
  connections: KnowledgeConnection[];
  busyKey: string | null;
  mutate: CatalogMutation;
  onRotateCredential: (connection: KnowledgeConnection) => void;
}) {
  if (connections.length === 0) {
    return (
      <div className={workbenchSurface.catalog.emptyState}>
        <Database className={workbenchSurface.catalog.emptyIcon} />
        <h2 className={workbenchSurface.catalog.emptyTitle}>尚未连接 RAGFlow</h2>
        <p className={workbenchSurface.catalog.emptyDescription}>
          新建连接后先执行认证检查，再激活并同步数据集目录。
        </p>
      </div>
    );
  }

  return (
    <div className={workbenchSurface.catalog.cardGrid}>
      {connections.map((connection) => {
        const busy = busyKey?.endsWith(`:${connection.id}`) ?? false;
        const checkBusy = busyKey === `check:${connection.id}`;
        const activateBusy = busyKey === `activate:${connection.id}`;
        const syncBusy = busyKey === `sync:${connection.id}`;
        return (
          <article
            key={connection.id}
            className={workbenchSurface.catalog.entryCard}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <h2 className="truncate text-sm font-semibold text-[var(--theme-text)]">
                  {connection.name}
                </h2>
                <p className="mt-1 truncate text-xs text-[var(--theme-text-secondary)]">
                  {connection.base_url}
                </p>
              </div>
              <StatusBadge status={connection.status} />
            </div>
            <dl className="mt-4 grid grid-cols-2 gap-2 text-xs">
              <div className={workbenchSurface.catalog.metricTile}>
                <dt className={workbenchSurface.catalog.label}>知识源</dt>
                <dd className="mt-1 font-medium text-[var(--theme-text)]">
                  {connection.source_count}
                </dd>
              </div>
              <div className={workbenchSurface.catalog.metricTile}>
                <dt className={workbenchSurface.catalog.label}>凭据状态</dt>
                <dd className="mt-1 truncate text-[var(--theme-text)]">
                  已安全保存 · {connection.credential_fingerprint || "无指纹"}
                </dd>
              </div>
            </dl>
            <p className="mt-3 text-xs text-[var(--theme-text-secondary)]">
              最近认证：{safeDate(connection.last_authenticated_check_at)}
              <span aria-hidden="true"> · </span>
              最近同步：{safeDate(connection.last_complete_sync_at)}
            </p>
            {connection.safe_failure_code ? (
              <p className="mt-2 rounded-md bg-red-500/10 px-2 py-1.5 text-xs text-red-700 dark:text-red-300">
                {connection.safe_failure_code}
              </p>
            ) : null}
            <div className="mt-4 flex flex-wrap gap-2 border-t border-[var(--theme-border)] pt-3">
              <button
                className="btn-secondary"
                type="button"
                disabled={busy}
                onClick={() => onRotateCredential(connection)}
              >
                <KeyRound size={15} /> 更新凭据
              </button>
              {connection.candidate_revision_id ? (
                <>
                  <button
                    className="btn-secondary"
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      void mutate(
                        `check:${connection.id}`,
                        () => knowledgeApi.checkConnection(connection.id),
                        "认证检查通过",
                      )
                    }
                  >
                    {checkBusy ? (
                      <Loader2 className="animate-spin" size={15} />
                    ) : (
                      <CheckCircle2 size={15} />
                    )}
                    {checkBusy ? "检查中" : "检查"}
                  </button>
                  <button
                    className="btn-primary"
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      void mutate(
                        `activate:${connection.id}`,
                        () => knowledgeApi.activateConnection(connection.id),
                        "连接已激活，目录同步完成",
                      )
                    }
                  >
                    {activateBusy ? (
                      <Loader2 className="animate-spin" size={15} />
                    ) : (
                      <Database size={15} />
                    )}
                    激活并同步
                  </button>
                </>
              ) : null}
              {connection.status === "active" ? (
                <>
                  <button
                    className="btn-secondary"
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      void mutate(
                        `sync:${connection.id}`,
                        () => knowledgeApi.syncConnection(connection.id),
                        "知识源目录已同步",
                      )
                    }
                  >
                    {syncBusy ? (
                      <Loader2 className="animate-spin" size={15} />
                    ) : (
                      <RefreshCw size={15} />
                    )}
                    {syncBusy ? "同步中" : "同步目录"}
                  </button>
                  <button
                    className="btn-secondary"
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      void mutate(
                        `disable:${connection.id}`,
                        () => knowledgeApi.disableConnection(connection.id),
                        "连接已停用",
                      )
                    }
                  >
                    停用
                  </button>
                </>
              ) : null}
            </div>
          </article>
        );
      })}
    </div>
  );
}

export function SourceCatalog({
  sources,
  busyKey,
  mutate,
  onEdit,
  onEditAcl,
}: {
  sources: KnowledgeSource[];
  busyKey: string | null;
  mutate: CatalogMutation;
  onEdit: (source: KnowledgeSource) => void;
  onEditAcl: (source: KnowledgeSource) => void;
}) {
  if (sources.length === 0) {
    return (
      <div className={workbenchSurface.catalog.emptyState}>
        <ShieldCheck className={workbenchSurface.catalog.emptyIcon} />
        <h2 className={workbenchSurface.catalog.emptyTitle}>暂无知识源</h2>
        <p className={workbenchSurface.catalog.emptyDescription}>
          激活 RAGFlow 连接后，已解析的数据集会作为逻辑知识源出现在这里。
        </p>
      </div>
    );
  }

  return (
    <div className={workbenchSurface.catalog.cardGrid}>
      {sources.map((source) => {
        const busy = busyKey?.endsWith(`:${source.id}`) ?? false;
        const syncBusy = busyKey === `sync:${source.id}`;
        const aclReady =
          source.visibility === "enterprise" ||
          source.allowed_department_ids.length > 0 ||
          source.allowed_roles.length > 0 ||
          source.allowed_user_ids.length > 0;
        return (
          <article key={source.id} className={workbenchSurface.catalog.entryCard}>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <h2 className="truncate text-sm font-semibold text-[var(--theme-text)]">
                  {source.name}
                </h2>
                <p className="mt-1 truncate text-xs text-[var(--theme-text-secondary)]">
                  {source.connection_name}
                </p>
              </div>
              <StatusBadge status={source.status} />
            </div>
            {source.description ? (
              <p className="mt-3 line-clamp-2 text-xs leading-5 text-[var(--theme-text-secondary)]">
                {source.description}
              </p>
            ) : null}
            <div className="mt-3 rounded-md bg-[var(--theme-bg-sidebar)] p-3 text-xs text-[var(--theme-text-secondary)]">
              {source.visibility === "enterprise"
                ? "全公司可用"
                : source.allowed_department_ids.length > 0
                  ? `限定 ${source.allowed_department_ids.length} 个部门`
                  : "限定部门尚未配置"}
            </div>
            <p className="mt-3 text-xs text-[var(--theme-text-secondary)]">
              连接状态：{source.connection_status === "active" ? "可用" : "不可用"}
              <span aria-hidden="true"> · </span>
              最近完整同步：{safeDate(source.last_complete_sync_at)}
            </p>
            <div className="mt-4 flex flex-wrap gap-2 border-t border-[var(--theme-border)] pt-3">
              <button
                className="btn-secondary"
                type="button"
                disabled={busy}
                onClick={() => onEdit(source)}
              >
                <Pencil size={15} /> 编辑信息
              </button>
              <button
                className="btn-secondary"
                type="button"
                disabled={busy}
                onClick={() => onEditAcl(source)}
              >
                <ShieldCheck size={15} /> 部门权限
              </button>
              {source.status !== "active" && source.status !== "missing" ? (
                <button
                  className="btn-primary"
                  type="button"
                  disabled={busy || !aclReady}
                  title={aclReady ? undefined : "请先配置可见范围"}
                  onClick={() =>
                    void mutate(
                      `activate:${source.id}`,
                      () => knowledgeApi.updateSource(source.id, { status: "active" }),
                      "知识源已启用",
                    )
                  }
                >
                  启用
                </button>
              ) : null}
              {source.status === "active" ? (
                <>
                  <button
                    className="btn-secondary"
                    type="button"
                    disabled={busy || source.connection_status !== "active"}
                    title={
                      source.connection_status === "active"
                        ? undefined
                        : "连接当前不可用"
                    }
                    onClick={() =>
                      void mutate(
                        `sync:${source.id}`,
                        () => knowledgeApi.syncConnection(source.connection_id),
                        "知识源目录已同步",
                      )
                    }
                  >
                    {syncBusy ? (
                      <Loader2 className="animate-spin" size={15} />
                    ) : (
                      <RefreshCw size={15} />
                    )}
                    同步连接
                  </button>
                  <button
                    className="btn-secondary"
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      void mutate(
                        `disable:${source.id}`,
                        () => knowledgeApi.updateSource(source.id, { status: "disabled" }),
                        "知识源已停用",
                      )
                    }
                  >
                    停用
                  </button>
                </>
              ) : null}
            </div>
          </article>
        );
      })}
    </div>
  );
}
