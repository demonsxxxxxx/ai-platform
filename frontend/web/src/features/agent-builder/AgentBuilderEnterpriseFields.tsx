import { useEffect, useState } from "react";
import { Building2, MessageSquareText, ShieldCheck } from "lucide-react";

import { DepartmentDirectorySelector } from "../../components/panels/DepartmentDirectorySelector";
import {
  capabilityDistributionApi,
  type DepartmentDirectoryNode,
} from "../../services/api/capabilityDistribution";
import type { AgentBuilderEditor } from "./agentBuilderAdapter";
import { AgentAvatarPicker } from "./AgentAvatarPicker";

const INPUT_CLASS =
  "w-full rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-3 py-2 text-sm outline-none focus:border-[var(--theme-primary)] focus:ring-1 focus:ring-[var(--theme-primary)] disabled:cursor-not-allowed disabled:opacity-60";

function lines(value: string): string[] {
  return value
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter((item, index, all) => item && all.indexOf(item) === index);
}

function lineValue(value: readonly string[]): string {
  return value.join("\n");
}

function ListField({
  className,
  disabled,
  label,
  onChange,
  values,
}: {
  className?: string;
  disabled: boolean;
  label: string;
  onChange: (values: string[]) => void;
  values: readonly string[];
}) {
  const canonicalValue = lineValue(values);
  const [draft, setDraft] = useState(canonicalValue);
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    if (!editing) setDraft(canonicalValue);
  }, [canonicalValue, editing]);

  const commit = () => {
    const normalized = lines(draft);
    setDraft(lineValue(normalized));
    onChange(normalized);
  };

  return (
    <label className="flex flex-col gap-2">
      <span className="text-sm font-medium">{label}</span>
      <textarea
        aria-label={label}
        className={`${INPUT_CLASS} resize-y ${className ?? "min-h-24"}`}
        disabled={disabled}
        onBlur={() => {
          commit();
          setEditing(false);
        }}
        onChange={(event) => {
          setDraft(event.target.value);
          onChange(lines(event.target.value));
        }}
        onFocus={() => setEditing(true)}
        value={draft}
      />
    </label>
  );
}

export function AgentBuilderEnterpriseFields({
  disabled,
  editor,
  marketTagSuggestions = [],
  onChange,
}: {
  disabled: boolean;
  editor: AgentBuilderEditor;
  marketTagSuggestions?: readonly string[];
  onChange: (update: Partial<AgentBuilderEditor>) => void;
}) {
  const [directory, setDirectory] = useState<DepartmentDirectoryNode[] | null>(
    null,
  );
  const [directoryError, setDirectoryError] = useState<string | null>(null);

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
        setDirectoryError("部门目录暂不可用，已保留服务端选择。");
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <>
      <section
        aria-labelledby="agent-enterprise-heading"
        className="rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-5"
        data-agent-builder-market-settings
      >
        <div className="flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-[var(--theme-primary-light)] text-[var(--theme-primary)]">
            <Building2 aria-hidden="true" size={17} />
          </span>
          <div>
            <h3 className="text-sm font-semibold" id="agent-enterprise-heading">
              市场展示与任务入口
            </h3>
            <p className="mt-1 text-sm leading-6 text-[var(--theme-text-secondary)]">
              配置员工在专家市场看到的信息、开场白和可以直接开始的任务。
            </p>
          </div>
        </div>
        <div className="mt-5 grid gap-5 lg:grid-cols-[minmax(0,1.15fr)_minmax(14rem,0.85fr)]">
          <div className="grid gap-4">
            <label className="flex flex-col gap-2">
              <span className="text-sm font-medium">专家简介</span>
              <textarea
                aria-label="专家简介"
                className={`${INPUT_CLASS} min-h-24 resize-y`}
                disabled={disabled}
                onChange={(event) => onChange({ description: event.target.value })}
                placeholder="用一句话说明这位专家适合解决什么问题"
                value={editor.description}
              />
            </label>
            <label className="flex flex-col gap-2">
              <span className="text-sm font-medium">能力摘要</span>
              <textarea
                className={`${INPUT_CLASS} min-h-28 resize-y`}
                disabled={disabled}
                onChange={(event) => onChange({ capabilitySummary: event.target.value })}
                placeholder="说明能力范围和交付方式"
                value={editor.capabilitySummary}
              />
            </label>
          </div>
          <div className="grid content-start gap-4 rounded-lg bg-[var(--theme-bg-sidebar)] p-4 ring-1 ring-[var(--theme-border)]">
            <AgentAvatarPicker
              agentId={(editor.agentId ?? editor.name) || "new-expert"}
              avatarRef={editor.avatarRef}
              avatarSeed={editor.avatarSeed}
              disabled={disabled}
              name={editor.name || "未命名专家"}
              onChange={(update) => onChange(update)}
            />
            <label className="flex flex-col gap-2">
              <span className="text-sm font-medium">市场标签</span>
              <input
                aria-label="市场标签"
                className={INPUT_CLASS}
                disabled={disabled}
                list="agent-market-tag-suggestions"
                maxLength={80}
                onChange={(event) => onChange({ marketTag: event.target.value })}
                placeholder="例如：人力资源"
                value={editor.marketTag}
              />
              <datalist id="agent-market-tag-suggestions">
                {marketTagSuggestions.map((tag) => <option key={tag} value={tag} />)}
              </datalist>
            </label>
          </div>
        </div>

        <div className="mt-5 border-t border-[var(--theme-border)] pt-5">
          <div className="mb-4 flex items-center gap-2">
            <MessageSquareText aria-hidden="true" className="text-[var(--theme-text-secondary)]" size={17} />
            <h4 className="text-sm font-semibold">对话开场</h4>
          </div>
          <label className="flex flex-col gap-2">
              <span className="text-sm font-medium">欢迎语</span>
              <textarea
                className={`${INPUT_CLASS} min-h-20 resize-y`}
                disabled={disabled}
                onChange={(event) => onChange({ welcomeMessage: event.target.value })}
                placeholder="员工打开专家工作区时看到的开场白"
                value={editor.welcomeMessage}
              />
          </label>
          <div className="mt-4 grid gap-4 lg:grid-cols-3">
            <ListField
              className="min-h-28"
              disabled={disabled}
              label="推荐任务（可选）"
              onChange={(recommendedTasks) => onChange({ recommendedTasks })}
              values={editor.recommendedTasks}
            />
            <ListField
              className="min-h-28"
              disabled={disabled}
              label="示例问题（可选）"
              onChange={(starterPrompts) => onChange({ starterPrompts })}
              values={editor.starterPrompts}
            />
            <ListField
              disabled={disabled}
              label="预期输出（可选）"
              onChange={(expectedOutputs) => onChange({ expectedOutputs })}
              values={editor.expectedOutputs}
            />
          </div>
        </div>
      </section>

      <section
        aria-label="访问范围与数据说明（高级）"
        className="rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-5"
      >
        <details
          data-agent-builder-access-settings
        >
          <summary className="flex cursor-pointer items-center gap-2 text-sm font-medium">
            <ShieldCheck
              aria-hidden="true"
              className="text-[var(--theme-text-secondary)]"
              size={17}
            />
            访问范围与数据说明（高级）
          </summary>
          <p className="mt-3 text-sm leading-6 text-[var(--theme-text-secondary)]">
            默认对公司内部用户开放；需要限制部门、角色或用户时再展开配置。
          </p>
        <label className="mt-4 flex max-w-sm flex-col gap-2">
          <span className="text-sm font-medium">可见范围</span>
          <select
            className={INPUT_CLASS}
            disabled={disabled}
            onChange={(event) =>
              onChange({
                visibility: event.target.value as AgentBuilderEditor["visibility"],
              })
            }
            value={editor.visibility}
          >
            <option value="tenant">全公司</option>
            <option value="restricted">指定部门、角色或用户</option>
          </select>
        </label>

        {editor.visibility === "restricted" ? (
          <div className="mt-4 grid gap-4">
            <div>
              <span className="mb-2 block text-sm font-medium">允许部门</span>
              <DepartmentDirectorySelector
                directory={directory}
                disabled={disabled}
                loadError={directoryError}
                onChange={(allowedDepartmentIds) =>
                  onChange({ allowedDepartmentIds })
                }
                selectedAuthorityIds={editor.allowedDepartmentIds}
              />
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <ListField
                disabled={disabled}
                label="允许角色"
                onChange={(allowedRoles) => onChange({ allowedRoles })}
                values={editor.allowedRoles}
              />
              <ListField
                disabled={disabled}
                label="允许用户"
                onChange={(allowedUserIds) => onChange({ allowedUserIds })}
                values={editor.allowedUserIds}
              />
            </div>
          </div>
        ) : null}

        <label className="mt-4 flex flex-col gap-2">
          <span className="text-sm font-medium">权限与数据访问说明</span>
          <textarea
            className={`${INPUT_CLASS} min-h-28 resize-y`}
            disabled={disabled}
            onChange={(event) =>
              onChange({ permissionsAndDataAccessNotice: event.target.value })
            }
            value={editor.permissionsAndDataAccessNotice}
          />
        </label>
        </details>
      </section>
    </>
  );
}
