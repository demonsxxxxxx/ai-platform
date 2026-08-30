import { Loader2, Save, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import toast from "react-hot-toast";

import { DepartmentDirectorySelector } from "../../../components/panels/DepartmentDirectorySelector";
import { knowledgeApi } from "../../../services/api";
import {
  capabilityDistributionApi,
  type DepartmentDirectoryNode,
} from "../../../services/api/capabilityDistribution";
import type { KnowledgeSource } from "../../../types/knowledge";

type FormCompletion = () => Promise<void>;

export function SourceDetailsForm({
  source,
  onClose,
  onSaved,
}: {
  source: KnowledgeSource;
  onClose: () => void;
  onSaved: FormCompletion;
}) {
  const initialDisplayName = source.name === source.provider_name ? "" : source.name;
  const [displayName, setDisplayName] = useState(initialDisplayName);
  const [description, setDescription] = useState(source.description);
  const [submitting, setSubmitting] = useState(false);

  return (
    <form
      className="space-y-4 p-5"
      onSubmit={async (event) => {
        event.preventDefault();
        setSubmitting(true);
        try {
          await knowledgeApi.updateSource(source.id, {
            display_name: displayName.trim() || null,
            description: description.trim() || null,
          });
          await onSaved();
        } catch (caught) {
          toast.error(caught instanceof Error ? caught.message : "知识源信息保存失败");
        } finally {
          setSubmitting(false);
        }
      }}
    >
      <div className="rounded-lg bg-[var(--theme-bg-sidebar)] p-3 text-xs text-[var(--theme-text-secondary)]">
        RAGFlow 原始名称：{source.provider_name}
      </div>
      <label className="block text-sm font-medium text-[var(--theme-text)]">
        显示名称
        <input
          autoFocus
          maxLength={240}
          className="input-field mt-2 w-full"
          value={displayName}
          onChange={(event) => setDisplayName(event.target.value)}
          placeholder="留空则使用 RAGFlow 原始名称"
        />
      </label>
      <label className="block text-sm font-medium text-[var(--theme-text)]">
        使用说明
        <textarea
          maxLength={1000}
          rows={5}
          className="input-field mt-2 w-full resize-y"
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          placeholder="说明知识范围、适用场景和使用边界"
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
            <Save size={16} />
          )}
          保存信息
        </button>
      </div>
    </form>
  );
}

export function SourceAclForm({
  source,
  onClose,
  onSaved,
}: {
  source: KnowledgeSource;
  onClose: () => void;
  onSaved: FormCompletion;
}) {
  const [visibility, setVisibility] = useState<"enterprise" | "restricted">(
    source.visibility,
  );
  const [departments, setDepartments] = useState(source.allowed_department_ids);
  const [directory, setDirectory] = useState<DepartmentDirectoryNode[] | null>(
    null,
  );
  const [directoryError, setDirectoryError] = useState<string | null>(null);
  const [directoryAttempt, setDirectoryAttempt] = useState(0);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let active = true;
    void capabilityDistributionApi
      .departmentDirectory()
      .then((value) => {
        if (!active) return;
        setDirectory(value);
        setDirectoryError(null);
      })
      .catch(() => {
        if (!active) return;
        setDirectory(null);
        setDirectoryError("部门目录暂不可用，当前选择已保留。");
      });
    return () => {
      active = false;
    };
  }, [directoryAttempt]);

  return (
    <form
      className="space-y-4 p-5"
      onSubmit={async (event) => {
        event.preventDefault();
        if (visibility === "restricted" && departments.length === 0) {
          toast.error("限定部门时至少选择一个部门");
          return;
        }
        setSubmitting(true);
        try {
          await knowledgeApi.replaceSourceAcl(source.id, {
            expected_authorization_version: source.authorization_version,
            visibility,
            department_ids: visibility === "enterprise" ? [] : departments,
          });
          await onSaved();
        } catch (caught) {
          toast.error(caught instanceof Error ? caught.message : "权限保存失败");
        } finally {
          setSubmitting(false);
        }
      }}
    >
      <div>
        <p className="text-sm font-semibold text-[var(--theme-text)]">
          {source.name}
        </p>
        <p className="mt-1 text-xs text-[var(--theme-text-secondary)]">
          部门来自公司的权威目录，保存时服务端会重新校验当前目录。
        </p>
      </div>
      <fieldset className="space-y-2">
        <legend className="text-sm font-medium text-[var(--theme-text)]">
          可见范围
        </legend>
        <label className="flex items-start gap-3 rounded-lg border border-[var(--theme-border)] p-3">
          <input
            type="radio"
            name="visibility"
            checked={visibility === "enterprise"}
            onChange={() => setVisibility("enterprise")}
          />
          <span>
            <span className="block text-sm font-medium text-[var(--theme-text)]">
              全公司可用
            </span>
            <span className="block text-xs text-[var(--theme-text-secondary)]">
              所有已登录用户均可被授权使用
            </span>
          </span>
        </label>
        <label className="flex items-start gap-3 rounded-lg border border-[var(--theme-border)] p-3">
          <input
            type="radio"
            name="visibility"
            checked={visibility === "restricted"}
            onChange={() => setVisibility("restricted")}
          />
          <span>
            <span className="block text-sm font-medium text-[var(--theme-text)]">
              限定部门
            </span>
            <span className="block text-xs text-[var(--theme-text-secondary)]">
              仅配置的部门可以在智能体中使用
            </span>
          </span>
        </label>
      </fieldset>
      {visibility === "restricted" ? (
        <div>
          <span className="mb-2 block text-sm font-medium text-[var(--theme-text)]">
            允许部门
          </span>
          <DepartmentDirectorySelector
            directory={directory}
            disabled={submitting}
            loadError={directoryError}
            onChange={setDepartments}
            selectedAuthorityIds={departments}
          />
          {directoryError ? (
            <button
              className="btn-secondary mt-2"
              onClick={() => setDirectoryAttempt((value) => value + 1)}
              type="button"
            >
              重新加载部门目录
            </button>
          ) : null}
        </div>
      ) : null}
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
            <ShieldCheck size={16} />
          )}
          保存权限
        </button>
      </div>
    </form>
  );
}
