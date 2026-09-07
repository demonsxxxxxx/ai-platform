import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type FocusEvent,
  type KeyboardEvent,
} from "react";
import { Building2, ChevronDown, MessageSquareText, ShieldCheck } from "lucide-react";

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

function normalizeTag(value: string): string {
  return value.trim().normalize("NFKC").toLocaleLowerCase();
}

function filterMarketTagSuggestions(
  suggestions: readonly string[],
  query: string,
): string[] {
  const normalizedQuery = normalizeTag(query);
  return suggestions.filter(
    (tag) => !normalizedQuery || normalizeTag(tag).includes(normalizedQuery),
  );
}

function MarketTagCombobox({
  disabled,
  id,
  onChange,
  suggestions,
  value,
}: {
  disabled: boolean;
  id: string;
  onChange: (value: string) => void;
  suggestions: readonly string[];
  value: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(-1);
  const rootRef = useRef<HTMLDivElement>(null);
  const listboxId = useId();
  const filteredSuggestions = useMemo(
    () => filterMarketTagSuggestions(suggestions, query),
    [query, suggestions],
  );
  const safeActiveIndex =
    activeIndex >= 0 && activeIndex < filteredSuggestions.length
      ? activeIndex
      : -1;

  useEffect(() => {
    if (!open) return;
    const closeOnOutsideClick = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
        setActiveIndex(-1);
      }
    };
    document.addEventListener("mousedown", closeOnOutsideClick);
    return () => document.removeEventListener("mousedown", closeOnOutsideClick);
  }, [open]);

  const openSuggestions = () => {
    setQuery("");
    setOpen(true);
    setActiveIndex(-1);
  };

  const chooseSuggestion = (tag: string) => {
    onChange(tag);
    setQuery(tag);
    setOpen(false);
    setActiveIndex(-1);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
      setActiveIndex(-1);
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (!open) {
        openSuggestions();
        return;
      }
      if (filteredSuggestions.length > 0) {
        setActiveIndex((current) =>
          current < filteredSuggestions.length - 1 ? current + 1 : 0,
        );
      }
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) {
        openSuggestions();
        return;
      }
      if (filteredSuggestions.length > 0) {
        setActiveIndex((current) =>
          current > 0 ? current - 1 : filteredSuggestions.length - 1,
        );
      }
      return;
    }
    if (event.key === "Enter" && open) {
      event.preventDefault();
      if (safeActiveIndex >= 0) {
        chooseSuggestion(filteredSuggestions[safeActiveIndex]);
      } else {
        setOpen(false);
      }
      return;
    }
    if (event.key === "Home" && open && filteredSuggestions.length > 0) {
      event.preventDefault();
      setActiveIndex(0);
    } else if (event.key === "End" && open && filteredSuggestions.length > 0) {
      event.preventDefault();
      setActiveIndex(filteredSuggestions.length - 1);
    }
  };

  return (
    <div
      className="relative"
      onBlur={(event: FocusEvent<HTMLDivElement>) => {
        const nextTarget = event.relatedTarget as Node | null;
        if (!rootRef.current?.contains(nextTarget)) {
          setOpen(false);
          setActiveIndex(-1);
        }
      }}
      ref={rootRef}
    >
      <input
        aria-activedescendant={
          safeActiveIndex >= 0
            ? `${listboxId}-option-${safeActiveIndex}`
            : undefined
        }
        aria-autocomplete="list"
        aria-controls={listboxId}
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-label="市场标签"
        className={`${INPUT_CLASS} pr-9`}
        disabled={disabled}
        id={id}
        maxLength={80}
        onChange={(event) => {
          onChange(event.target.value);
          setQuery(event.target.value);
          setOpen(true);
          setActiveIndex(-1);
        }}
        onFocus={openSuggestions}
        onKeyDown={handleKeyDown}
        placeholder="例如：人力资源"
        role="combobox"
        value={value}
      />
      <ChevronDown
        aria-hidden="true"
        className={`pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[var(--theme-text-secondary)] transition-transform ${open ? "rotate-180" : ""}`}
        size={16}
      />
      {open ? (
        <div
          aria-label="已有市场标签"
          className="absolute left-0 right-0 top-full z-20 mt-1 max-h-52 overflow-y-auto rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] py-1 shadow-lg"
          id={listboxId}
          role="listbox"
        >
          {filteredSuggestions.length > 0 ? (
            filteredSuggestions.map((tag, index) => (
              <button
                aria-selected={safeActiveIndex === index}
                className={`flex min-h-9 w-full items-center px-3 text-left text-sm transition-colors ${safeActiveIndex === index ? "bg-[var(--theme-primary-light)] text-[var(--theme-primary)]" : "text-[var(--theme-text)] hover:bg-[var(--theme-bg-sidebar)]"}`}
                id={`${listboxId}-option-${index}`}
                key={tag}
                onClick={() => chooseSuggestion(tag)}
                onMouseDown={(event) => event.preventDefault()}
                onMouseEnter={() => setActiveIndex(index)}
                role="option"
                type="button"
              >
                <span className="min-w-0 flex-1 truncate">{tag}</span>
              </button>
            ))
          ) : (
            <p className="px-3 py-2 text-xs text-[var(--theme-text-secondary)]">
              暂无匹配标签，可直接使用当前输入
            </p>
          )}
        </div>
      ) : null}
    </div>
  );
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
            <div className="flex flex-col gap-2">
              <label className="text-sm font-medium" htmlFor="agent-market-tag-input">
                市场标签
              </label>
              <MarketTagCombobox
                disabled={disabled}
                id="agent-market-tag-input"
                onChange={(marketTag) => onChange({ marketTag })}
                suggestions={marketTagSuggestions}
                value={editor.marketTag}
              />
            </div>
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
