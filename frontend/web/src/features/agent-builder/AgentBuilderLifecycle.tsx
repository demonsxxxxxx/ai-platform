import { useEffect, useState } from "react";
import { Archive, CircleAlert, FlaskConical, History, RefreshCw, Trash2 } from "lucide-react";

import { AgentBuilderDialog } from "../../components/agent-builder/AgentBuilderDialog";

import { agentProfileApi } from "../../services/api/agentProfile";
import type { AgentProfileAdminProjection } from "../../types";
import { isAgentProfileEditorDirty, listPublishedAgentProfileVersions, type AgentBuilderEditor } from "./agentBuilderAdapter";
import type { AgentBuilderMutationState } from "./agentBuilderController";

export function AgentBuilderLifecycle({
  disabled,
  editor,
  mutation,
  onRunTest,
  onUnpublish,
  onRetire,
}: {
  disabled: boolean;
  editor: AgentBuilderEditor;
  mutation: AgentBuilderMutationState;
  onRunTest: (message: string) => void;
  onUnpublish: (publishedRevision: number) => void;
  onRetire: () => void;
}) {
  const [history, setHistory] = useState<AgentProfileAdminProjection[]>([]);
  const [historyState, setHistoryState] = useState<"idle" | "loading" | "ready" | "error">(
    "idle",
  );
  const [testMessage, setTestMessage] = useState("");
  const [retireConfirmationOpen, setRetireConfirmationOpen] = useState(false);

  useEffect(() => {
    const agentId = editor.agentId;
    if (!agentId) {
      setHistory([]);
      setHistoryState("idle");
      return;
    }
    let active = true;
    setHistoryState("loading");
    void agentProfileApi
      .listHistory(agentId)
      .then((response) => {
        if (!active) return;
        setHistory(response.agent_profiles);
        setHistoryState("ready");
      })
      .catch(() => {
        if (!active) return;
        setHistory([]);
        setHistoryState("error");
      });
    return () => {
      active = false;
    };
  }, [editor.agentId, editor.revision]);

  const publishedVersions = listPublishedAgentProfileVersions(history);
  const cleanPublished =
    Boolean(editor.agentId) && editor.status === "published" && !isAgentProfileEditorDirty(editor);
  const historyPublishedRevision = editor.status === "withdrawn"
    ? null
    : publishedVersions.find(({ profile }) => profile.status === "published")?.profile.revision ?? null;
  const publishedRevision = editor.publishedRevision ?? historyPublishedRevision;
  const canUnpublish = Boolean(
    editor.agentId && publishedRevision && !isAgentProfileEditorDirty(editor),
  );
  const canRetire = Boolean(
    editor.agentId &&
      editor.revision &&
      editor.publishedRevision === null &&
      !isAgentProfileEditorDirty(editor),
  );
  const trialRun = mutation.phase === "success" && mutation.action === "test"
    ? mutation.trialRun
    : undefined;

  return (
    <section
      aria-labelledby="agent-lifecycle-heading"
      className="rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-5"
    >
      <div className="mb-4 flex items-center gap-2">
        <History
          aria-hidden="true"
          className="text-[var(--theme-text-secondary)]"
          size={17}
        />
        <h3 className="text-sm font-semibold" id="agent-lifecycle-heading">
          发布历史与试运行
        </h3>
      </div>

      {editor.agentId ? (
        <div className="overflow-x-auto border-y border-[var(--theme-border)]">
          <table className="w-full min-w-[34rem] text-left text-sm">
            <thead className="bg-[var(--theme-workbench-panel)] text-xs text-[var(--theme-text-secondary)]">
              <tr>
                <th className="px-3 py-2 font-medium" scope="col">发布版本</th>
                <th className="px-3 py-2 font-medium" scope="col">状态</th>
                <th className="px-3 py-2 font-medium" scope="col">content hash</th>
                <th className="px-3 py-2 font-medium" scope="col">发布时间</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--theme-border)]">
              {publishedVersions.map(({ profile, version }) => {
                const isCurrent = profile.revision === publishedRevision;
                const status = editor.status === "withdrawn"
                  ? "已下架"
                  : isCurrent
                    ? "当前发布"
                    : "历史版本";
                return (
                  <tr key={`${profile.agent_id}:${profile.revision}`}>
                    <td className="px-3 py-2 font-medium tabular-nums">v{version}</td>
                    <td className="px-3 py-2">{status}</td>
                    <td className="px-3 py-2 font-mono text-xs">
                      {profile.content_hash.slice(0, 12)}
                    </td>
                    <td className="px-3 py-2 text-[var(--theme-text-secondary)]">
                      {profile.published_at
                        ? new Date(profile.published_at).toLocaleString("zh-CN")
                        : "-"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {historyState === "loading" ? (
            <p className="px-3 py-3 text-sm text-[var(--theme-text-secondary)]">正在加载版本历史</p>
          ) : historyState === "error" ? (
            <p className="px-3 py-3 text-sm text-[var(--theme-danger)]" role="alert">
              版本历史暂不可用
            </p>
          ) : historyState === "ready" && publishedVersions.length === 0 ? (
            <p className="px-3 py-3 text-sm text-[var(--theme-text-secondary)]">
              暂无已发布版本，草稿保存不会增加发布版本号。
            </p>
          ) : null}
        </div>
      ) : (
        <p className="text-sm text-[var(--theme-text-secondary)]">保存后显示不可变版本历史</p>
      )}

      <div className="mt-5 grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto_auto_auto] sm:items-end">
        <label className="flex min-w-0 flex-col gap-2">
          <span className="text-sm font-medium">测试消息</span>
          <input
            className="h-10 w-full rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-3 text-sm outline-none focus:border-[var(--theme-primary)] focus:ring-1 focus:ring-[var(--theme-primary)] disabled:cursor-not-allowed disabled:opacity-60"
            disabled={disabled || !cleanPublished}
            onChange={(event) => setTestMessage(event.target.value)}
            value={testMessage}
          />
        </label>
        <button
          className="btn-secondary inline-flex items-center justify-center gap-2 disabled:cursor-not-allowed disabled:opacity-60"
          disabled={disabled || !cleanPublished || !testMessage.trim()}
          onClick={() => onRunTest(testMessage)}
          title="创建受控测试运行"
          type="button"
        >
          {mutation.phase === "testing" ? (
            <RefreshCw aria-hidden="true" className="animate-spin" size={16} />
          ) : (
            <FlaskConical aria-hidden="true" size={16} />
          )}
          {mutation.phase === "testing" ? "试运行中" : "真实试运行"}
        </button>
        <button
          className="btn-secondary inline-flex items-center justify-center gap-2 border-[var(--theme-danger)] text-[var(--theme-danger)] disabled:cursor-not-allowed disabled:opacity-60"
          disabled={disabled || !canUnpublish}
          onClick={() => publishedRevision && onUnpublish(publishedRevision)}
          title="下架当前发布版本"
          type="button"
        >
          {mutation.phase === "unpublishing" ? (
            <RefreshCw aria-hidden="true" className="animate-spin" size={16} />
          ) : (
            <Archive aria-hidden="true" size={16} />
          )}
          {mutation.phase === "unpublishing" ? "下架中" : "下架"}
        </button>
        <button
          aria-label={editor.publishedRevision ? "删除当前专家，请先下架" : "删除当前专家"}
          className="btn-secondary inline-flex items-center justify-center gap-2 border-[var(--theme-danger)] text-[var(--theme-danger)] disabled:cursor-not-allowed disabled:opacity-60"
          disabled={disabled || !canRetire}
          onClick={() => setRetireConfirmationOpen(true)}
          title={editor.publishedRevision ? "请先下架当前专家" : "删除当前专家"}
          type="button"
        >
          {mutation.phase === "deleting" ? (
            <RefreshCw aria-hidden="true" className="animate-spin" size={16} />
          ) : (
            <Trash2 aria-hidden="true" size={16} />
          )}
          {mutation.phase === "deleting" ? "删除中" : "删除"}
        </button>
      </div>

      {trialRun ? (
        <dl className="mt-4 grid gap-3 border-l-2 border-l-[var(--theme-success)] pl-3 text-sm sm:grid-cols-3">
          <div>
            <dt className="text-[var(--theme-text-secondary)]">测试会话</dt>
            <dd className="mt-1 break-all font-mono text-xs">{trialRun.session_id}</dd>
          </div>
          <div>
            <dt className="text-[var(--theme-text-secondary)]">测试 run</dt>
            <dd className="mt-1 break-all font-mono text-xs">{trialRun.run_id}</dd>
          </div>
          <div>
            <dt className="text-[var(--theme-text-secondary)]">状态</dt>
            <dd className="mt-1 font-medium">{trialRun.status}</dd>
          </div>
        </dl>
      ) : null}

      <AgentBuilderDialog
        descriptionId="agent-profile-retire-warning"
        isOpen={retireConfirmationOpen}
        onClose={() => setRetireConfirmationOpen(false)}
        title="删除专家？"
      >
        <div className="flex items-start gap-3">
          <CircleAlert
            aria-hidden="true"
            className="mt-0.5 shrink-0 text-[var(--theme-warning)]"
            size={19}
          />
          <p
            className="text-sm leading-6 text-[var(--theme-text-secondary)]"
            id="agent-profile-retire-warning"
          >
            删除后，该专家会从管理目录和用户历史导航中移除，且专家 ID 不可复用。不可变版本、历史运行、会话和审计记录仍会保留。
          </p>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button
            className="btn-secondary"
            onClick={() => setRetireConfirmationOpen(false)}
            type="button"
          >
            取消
          </button>
          <button
            className="btn-secondary border-[var(--theme-danger)] text-[var(--theme-danger)]"
            onClick={() => {
              setRetireConfirmationOpen(false);
              onRetire();
            }}
            type="button"
          >
            确认删除
          </button>
        </div>
      </AgentBuilderDialog>
    </section>
  );
}
