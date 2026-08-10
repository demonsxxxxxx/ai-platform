import { useEffect, useState } from "react";
import {
  Archive,
  FileUp,
  FlaskConical,
  History,
  RefreshCw,
  X,
} from "lucide-react";

import { useFileUpload } from "../../hooks/useFileUpload";
import { agentProfileApi } from "../../services/api/agentProfile";
import { uploadApi } from "../../services/api/upload";
import type { AgentProfileAdminProjection, MessageAttachment } from "../../types";
import {
  agentBuilderBlockReason,
  isAgentProfileEditorDirty,
  type AgentBuilderEditor,
  type AgentBuilderValidationIssue,
} from "./agentBuilderAdapter";
import type { AgentBuilderMutationState } from "./agentBuilderController";

function statusLabel(status: AgentProfileAdminProjection["status"]): string {
  if (status === "published") return "已发布";
  if (status === "withdrawn") return "已下架";
  return "草稿";
}

export function AgentBuilderLifecycle({
  disabled,
  editor,
  mutation,
  onRunTest,
  onUnpublish,
  testBlock,
}: {
  disabled: boolean;
  editor: AgentBuilderEditor;
  mutation: AgentBuilderMutationState;
  onRunTest: (message: string, fileIds: readonly string[]) => void;
  onUnpublish: () => void;
  testBlock: AgentBuilderValidationIssue | null;
}) {
  const [history, setHistory] = useState<AgentProfileAdminProjection[]>([]);
  const [historyState, setHistoryState] = useState<"idle" | "loading" | "ready" | "error">(
    "idle",
  );
  const [testMessage, setTestMessage] = useState("");
  const [attachments, setAttachments] = useState<MessageAttachment[]>([]);
  const { cancelUpload, uploadFiles } = useFileUpload({
    attachments,
    onAttachmentsChange: setAttachments,
  });

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

  useEffect(() => {
    setAttachments([]);
    setTestMessage("");
  }, [editor.agentId, editor.revision]);

  const cleanPublished =
    Boolean(editor.agentId) && editor.status === "published" && !isAgentProfileEditorDirty(editor);
  const trialRun = mutation.phase === "success" && mutation.action === "test"
    ? mutation.trialRun
    : undefined;
  const supportsFiles = editor.supportedInputTypes.includes("file");
  const hasUploadingAttachment = attachments.some((attachment) => attachment.isUploading);
  const hasInvalidAttachment = attachments.some(
    (attachment) =>
      !attachment.isUploading && !/^file_[A-Za-z0-9._:-]+$/.test(attachment.key),
  );
  const readyFileIds = Array.from(
    new Set(
      attachments
        .filter(
          (attachment) =>
            !attachment.isUploading && /^file_[A-Za-z0-9._:-]+$/.test(attachment.key),
        )
        .map((attachment) => attachment.key),
    ),
  );
  const acceptedFileTypes = editor.supportedFileTypes
    .map((fileType) => fileType.trim())
    .filter(Boolean)
    .map((fileType) =>
      fileType.includes("/") || fileType.startsWith(".") ? fileType : `.${fileType}`,
    )
    .join(",");

  useEffect(() => {
    if (!trialRun) return;
    setAttachments([]);
    setTestMessage("");
  }, [trialRun]);

  const removeAttachment = (attachment: MessageAttachment) => {
    if (attachment.isUploading) {
      cancelUpload(attachment.id);
      return;
    }
    setAttachments((current) =>
      current.filter((candidate) => candidate.id !== attachment.id),
    );
    if (attachment.key) void uploadApi.deleteFile(attachment.key).catch(() => undefined);
  };

  return (
    <section
      aria-labelledby="agent-lifecycle-heading"
      className="border-t border-[var(--theme-border)] py-6"
    >
      <div className="mb-4 flex items-center gap-2">
        <History
          aria-hidden="true"
          className="text-[var(--theme-text-secondary)]"
          size={17}
        />
        <h3 className="text-sm font-semibold" id="agent-lifecycle-heading">
          版本历史与试运行
        </h3>
      </div>

      {editor.agentId ? (
        <div className="overflow-x-auto border-y border-[var(--theme-border)]">
          <table className="w-full min-w-[34rem] text-left text-sm">
            <thead className="bg-[var(--theme-workbench-panel)] text-xs text-[var(--theme-text-secondary)]">
              <tr>
                <th className="px-3 py-2 font-medium" scope="col">revision</th>
                <th className="px-3 py-2 font-medium" scope="col">状态</th>
                <th className="px-3 py-2 font-medium" scope="col">content hash</th>
                <th className="px-3 py-2 font-medium" scope="col">创建时间</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--theme-border)]">
              {history.map((profile) => (
                <tr key={`${profile.agent_id}:${profile.revision}`}>
                  <td className="px-3 py-2 font-medium tabular-nums">{profile.revision}</td>
                  <td className="px-3 py-2">{statusLabel(profile.status)}</td>
                  <td className="px-3 py-2 font-mono text-xs">
                    {profile.content_hash.slice(0, 12)}
                  </td>
                  <td className="px-3 py-2 text-[var(--theme-text-secondary)]">
                    {profile.created_at
                      ? new Date(profile.created_at).toLocaleString("zh-CN")
                      : "-"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {historyState === "loading" ? (
            <p className="px-3 py-3 text-sm text-[var(--theme-text-secondary)]">正在加载版本历史</p>
          ) : historyState === "error" ? (
            <p className="px-3 py-3 text-sm text-[var(--theme-danger)]" role="alert">
              版本历史暂不可用
            </p>
          ) : historyState === "ready" && history.length === 0 ? (
            <p className="px-3 py-3 text-sm text-[var(--theme-text-secondary)]">暂无服务端版本</p>
          ) : null}
        </div>
      ) : (
        <p className="text-sm text-[var(--theme-text-secondary)]">保存后显示不可变版本历史</p>
      )}

      <p
        className={`mt-4 text-sm ${testBlock ? "text-[var(--theme-text-secondary)]" : "text-[var(--theme-success)]"}`}
        data-agent-builder-test-reason
      >
        {testBlock
          ? `真实试运行：${agentBuilderBlockReason(testBlock)}`
          : `已保存草稿 revision ${editor.revision}，content hash ${editor.materializedProfile?.content_hash.slice(0, 12)} 已锁定。`}
      </p>

      {supportsFiles ? (
        <div className="mt-4 space-y-3">
          <label className="inline-flex w-fit cursor-pointer items-center gap-2 text-sm font-medium text-[var(--theme-primary)] disabled:cursor-not-allowed">
            <FileUp aria-hidden="true" size={16} />
            添加测试附件
            <input
              accept={acceptedFileTypes || undefined}
              aria-label="测试附件"
              className="sr-only"
              disabled={disabled || testBlock !== null}
              multiple
              onChange={(event) => {
                if (event.target.files) uploadFiles(event.target.files);
                event.target.value = "";
              }}
              type="file"
            />
          </label>
          {attachments.length > 0 ? (
            <ul className="divide-y divide-[var(--theme-border)] border-y border-[var(--theme-border)] text-sm">
              {attachments.map((attachment) => (
                <li className="flex min-w-0 items-center gap-3 py-2" key={attachment.id}>
                  <span className="min-w-0 flex-1 truncate">{attachment.name}</span>
                  {attachment.isUploading ? (
                    <span className="shrink-0 text-xs text-[var(--theme-text-secondary)]">
                      {attachment.uploadProgress ?? 0}%
                    </span>
                  ) : null}
                  <button
                    aria-label={`移除测试附件 ${attachment.name}`}
                    className="icon-btn shrink-0"
                    onClick={() => removeAttachment(attachment)}
                    title="移除附件"
                    type="button"
                  >
                    <X aria-hidden="true" size={15} />
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
          {hasInvalidAttachment ? (
            <p className="text-sm text-[var(--theme-danger)]" role="alert">
              上传结果缺少可提交的文件标识，请移除后重试。
            </p>
          ) : null}
        </div>
      ) : null}

      <div className="mt-5 grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto_auto] sm:items-end">
        <label className="flex min-w-0 flex-col gap-2">
          <span className="text-sm font-medium">测试消息</span>
          <input
            aria-label="测试消息"
            className="h-10 w-full rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-3 text-sm outline-none focus:border-[var(--theme-primary)] focus:ring-1 focus:ring-[var(--theme-primary)] disabled:cursor-not-allowed disabled:opacity-60"
            disabled={disabled || testBlock !== null}
            onChange={(event) => setTestMessage(event.target.value)}
            value={testMessage}
          />
        </label>
        <button
          className="btn-secondary inline-flex items-center justify-center gap-2 disabled:cursor-not-allowed disabled:opacity-60"
          disabled={
            disabled ||
            testBlock !== null ||
            !testMessage.trim() ||
            hasUploadingAttachment ||
            hasInvalidAttachment
          }
          onClick={() => onRunTest(testMessage, readyFileIds)}
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
          disabled={disabled || !cleanPublished}
          onClick={onUnpublish}
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
    </section>
  );
}
