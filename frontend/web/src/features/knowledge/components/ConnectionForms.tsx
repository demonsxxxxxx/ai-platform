import { KeyRound, Loader2, Plus } from "lucide-react";
import { useState } from "react";
import toast from "react-hot-toast";

import { knowledgeApi } from "../../../services/api";
import type { KnowledgeConnection } from "../../../types/knowledge";

type FormCompletion = () => Promise<void>;

export function ConnectionForm({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: FormCompletion;
}) {
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [credential, setCredential] = useState("");
  const [submitting, setSubmitting] = useState(false);

  return (
    <form
      className="space-y-4 p-5"
      onSubmit={async (event) => {
        event.preventDefault();
        setSubmitting(true);
        try {
          await knowledgeApi.createConnection({
            name,
            base_url: baseUrl,
            credential,
          });
          setCredential("");
          await onCreated();
        } catch (caught) {
          toast.error(caught instanceof Error ? caught.message : "连接保存失败");
        } finally {
          setSubmitting(false);
        }
      }}
    >
      <p className="text-sm leading-6 text-[var(--theme-text-secondary)]">
        连接公司已经部署并完成文档解析的 RAGFlow。平台只同步数据集目录，不负责上传或解析文档。
      </p>
      <label className="block text-sm font-medium text-[var(--theme-text)]">
        连接名称
        <input
          autoFocus
          required
          maxLength={120}
          className="input-field mt-2 w-full"
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="例如：公司制度知识库"
        />
      </label>
      <label className="block text-sm font-medium text-[var(--theme-text)]">
        RAGFlow 地址
        <input
          required
          type="url"
          className="input-field mt-2 w-full"
          value={baseUrl}
          onChange={(event) => setBaseUrl(event.target.value)}
          placeholder="https://ragflow.company.internal"
        />
      </label>
      <label className="block text-sm font-medium text-[var(--theme-text)]">
        API Key
        <input
          required
          type="password"
          autoComplete="new-password"
          className="input-field mt-2 w-full"
          value={credential}
          onChange={(event) => setCredential(event.target.value)}
          placeholder="仅本次写入，保存后不再回显"
        />
      </label>
      <div className="flex justify-end gap-2 border-t border-[var(--theme-border)] pt-4">
        <button
          className="btn-secondary"
          type="button"
          onClick={onClose}
          disabled={submitting}
        >
          取消
        </button>
        <button className="btn-primary" type="submit" disabled={submitting}>
          {submitting ? (
            <Loader2 className="animate-spin" size={16} />
          ) : (
            <Plus size={16} />
          )}
          保存连接
        </button>
      </div>
    </form>
  );
}

export function CredentialRotateForm({
  connection,
  onClose,
  onSaved,
}: {
  connection: KnowledgeConnection;
  onClose: () => void;
  onSaved: FormCompletion;
}) {
  const [credential, setCredential] = useState("");
  const [submitting, setSubmitting] = useState(false);

  return (
    <form
      className="space-y-4 p-5"
      onSubmit={async (event) => {
        event.preventDefault();
        setSubmitting(true);
        try {
          await knowledgeApi.rotateCredential(connection.id, credential);
          setCredential("");
          await onSaved();
        } catch (caught) {
          toast.error(caught instanceof Error ? caught.message : "凭据更新失败");
        } finally {
          setSubmitting(false);
        }
      }}
    >
      <div className="rounded-lg bg-[var(--theme-bg-sidebar)] p-3 text-sm text-[var(--theme-text-secondary)]">
        <p className="font-medium text-[var(--theme-text)]">{connection.name}</p>
        <p className="mt-1">保存后会生成待验证版本；检查并激活成功前，现有可用版本保持不变。</p>
      </div>
      <label className="block text-sm font-medium text-[var(--theme-text)]">
        新 API Key
        <input
          autoFocus
          required
          type="password"
          autoComplete="new-password"
          className="input-field mt-2 w-full"
          value={credential}
          onChange={(event) => setCredential(event.target.value)}
          placeholder="仅本次写入，保存后不再回显"
        />
      </label>
      <div className="flex justify-end gap-2 border-t border-[var(--theme-border)] pt-4">
        <button
          className="btn-secondary"
          type="button"
          onClick={onClose}
          disabled={submitting}
        >
          取消
        </button>
        <button className="btn-primary" type="submit" disabled={submitting}>
          {submitting ? (
            <Loader2 className="animate-spin" size={16} />
          ) : (
            <KeyRound size={16} />
          )}
          保存新凭据
        </button>
      </div>
    </form>
  );
}
