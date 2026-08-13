import { useEffect, useState } from "react";
import { Building2, ShieldCheck } from "lucide-react";

import { DepartmentDirectorySelector } from "../../components/panels/DepartmentDirectorySelector";
import {
  capabilityDistributionApi,
  type DepartmentDirectoryNode,
} from "../../services/api/capabilityDistribution";
import {
  AGENT_PROFILE_AVATAR_REFS,
  AGENT_PROFILE_CATEGORIES,
  AGENT_PROFILE_CATEGORY_LABELS,
} from "../../types/agentProfile";
import type { AgentBuilderEditor } from "./agentBuilderAdapter";

const INPUT_CLASS =
  "w-full rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-3 py-2 text-sm outline-none focus:border-[var(--theme-primary)] focus:ring-1 focus:ring-[var(--theme-primary)] disabled:cursor-not-allowed disabled:opacity-60";

const AVATAR_LABELS = {
  "builtin:agent": "智能体",
  "builtin:assistant": "服务支持",
  "builtin:document": "文档专家",
  "builtin:research": "研究分析",
} as const;

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

  useEffect(() => setDraft(canonicalValue), [canonicalValue]);

  const commit = () => {
    const normalized = lines(draft);
    setDraft(lineValue(normalized));
    onChange(normalized);
  };

  return (
    <label className="flex flex-col gap-2">
      <span className="text-sm font-medium">{label}</span>
      <textarea
        className={`${INPUT_CLASS} resize-y ${className ?? "min-h-24"}`}
        disabled={disabled}
        onBlur={commit}
        onChange={(event) => setDraft(event.target.value)}
        value={draft}
      />
    </label>
  );
}

export function AgentBuilderEnterpriseFields({
  disabled,
  editor,
  onChange,
}: {
  disabled: boolean;
  editor: AgentBuilderEditor;
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
        className="border-b border-[var(--theme-border)] py-6"
      >
        <div className="mb-4 flex items-center gap-2">
          <Building2
            aria-hidden="true"
            className="text-[var(--theme-text-secondary)]"
            size={17}
          />
          <h3 className="text-sm font-semibold" id="agent-enterprise-heading">
            可选配置
          </h3>
        </div>
        <p className="mb-4 text-sm leading-6 text-[var(--theme-text-secondary)]">
          首次创建只需要完成名称、Agent.md、模型和主 Skill；展示、文件与访问范围可稍后补充。
        </p>
        <details
          className="rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-4"
          data-agent-builder-market-settings
        >
          <summary className="cursor-pointer text-sm font-medium">
            市场展示与开场内容
          </summary>
          <p className="mt-3 text-sm leading-6 text-[var(--theme-text-secondary)]">
            这些字段只影响市场卡片、详情和开场体验，不改变执行能力。
          </p>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <label className="flex flex-col gap-2">
              <span className="text-sm font-medium">头像</span>
              <select
                className={INPUT_CLASS}
                disabled={disabled}
                onChange={(event) =>
                  onChange({
                    avatarRef: event.target.value as AgentBuilderEditor["avatarRef"],
                  })
                }
                value={editor.avatarRef}
              >
                {AGENT_PROFILE_AVATAR_REFS.map((avatar) => (
                  <option key={avatar} value={avatar}>
                    {AVATAR_LABELS[avatar]}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-2">
              <span className="text-sm font-medium">分类</span>
              <select
                className={INPUT_CLASS}
                disabled={disabled}
                onChange={(event) =>
                  onChange({
                    category: event.target.value as AgentBuilderEditor["category"],
                  })
                }
                value={editor.category}
              >
                {AGENT_PROFILE_CATEGORIES.map((category) => (
                  <option key={category} value={category}>
                    {AGENT_PROFILE_CATEGORY_LABELS[category]}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="mt-4 grid gap-4">
            <label className="flex flex-col gap-2">
              <span className="text-sm font-medium">简介</span>
              <textarea
                aria-label="智能体简介"
                className={`${INPUT_CLASS} min-h-20 resize-y`}
                disabled={disabled}
                onChange={(event) => onChange({ description: event.target.value })}
                value={editor.description}
              />
            </label>
            <label className="flex flex-col gap-2">
              <span className="text-sm font-medium">能力摘要</span>
              <textarea
                className={`${INPUT_CLASS} min-h-24 resize-y`}
                disabled={disabled}
                onChange={(event) => onChange({ capabilitySummary: event.target.value })}
                value={editor.capabilitySummary}
              />
            </label>
            <label className="flex flex-col gap-2">
              <span className="text-sm font-medium">欢迎语</span>
              <textarea
                className={`${INPUT_CLASS} min-h-20 resize-y`}
                disabled={disabled}
                onChange={(event) => onChange({ welcomeMessage: event.target.value })}
                value={editor.welcomeMessage}
              />
            </label>
          </div>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
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
        </details>

        <details
          className="mt-3 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-4"
          data-agent-builder-input-settings
        >
          <summary className="cursor-pointer text-sm font-medium">
            文件输入（可选）
          </summary>
          <p className="mt-3 text-sm leading-6 text-[var(--theme-text-secondary)]">
            文本输入始终可用；只有专家确实需要读取文件时才启用文件输入并声明类型。
          </p>
          <fieldset className="mt-4 min-w-0">
            <legend className="sr-only">输入能力</legend>
            <div className="mt-2 flex flex-wrap gap-4 text-sm">
              {(["text", "file"] as const).map((inputType) => (
                <label className="inline-flex items-center gap-2" key={inputType}>
                  <input
                    checked={editor.supportedInputTypes.includes(inputType)}
                    disabled={disabled || inputType === "text"}
                    onChange={(event) =>
                      onChange({
                        supportedInputTypes: event.target.checked
                          ? [...editor.supportedInputTypes, inputType]
                          : editor.supportedInputTypes.filter(
                              (item) => item !== inputType,
                            ),
                      })
                    }
                    type="checkbox"
                  />
                  {inputType === "text" ? "文本" : "文件"}
                </label>
              ))}
            </div>
            {editor.supportedInputTypes.includes("file") ? (
              <div className="mt-3 max-w-xl">
                <ListField
                  className="min-h-20"
                  disabled={disabled}
                  label="支持的文件类型"
                  onChange={(supportedFileTypes) => onChange({ supportedFileTypes })}
                  values={editor.supportedFileTypes}
                />
              </div>
            ) : null}
          </fieldset>
        </details>
      </section>

      <section
        aria-label="访问范围与数据说明（高级）"
        className="border-b border-[var(--theme-border)] py-6"
      >
        <details
          className="rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-4"
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
