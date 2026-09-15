import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type FocusEvent,
  type KeyboardEvent,
} from "react";
import { Building2, ChevronDown, MessageSquareText, ShieldCheck, X } from "lucide-react";

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

function parseMarketTags(value: string): string[] {
  return [...new Set(value.split(/[,，\n]/).map((tag) => tag.trim()).filter(Boolean))];
}

function serializeMarketTags(tags: readonly string[]): string {
  return [...new Set(tags.map((tag) => tag.trim()).filter(Boolean))].join("\n");
}

function addMarketTag(tags: readonly string[], value: string): string[] {
  const incoming = parseMarketTags(value);
  const next = [...tags];
  for (const tag of incoming) {
    if (!next.some((existing) => normalizeTag(existing) === normalizeTag(tag))) next.push(tag);
  }
  return next;
}

function filterMarketTagSuggestions(
  suggestions: readonly string[],
  query: string,
  selectedTags: readonly string[],
): string[] {
  const normalizedQuery = normalizeTag(query);
  return suggestions.filter(
    (tag) =>
      !selectedTags.some((selected) => normalizeTag(selected) === normalizeTag(tag)) &&
      (!normalizedQuery || normalizeTag(tag).includes(normalizedQuery)),
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
  const selectedTags = useMemo(() => parseMarketTags(value), [value]);
  const filteredSuggestions = useMemo(
    () => filterMarketTagSuggestions(suggestions, query, selectedTags),
    [query, selectedTags, suggestions],
  );
  const safeActiveIndex =
    activeIndex >= 0 && activeIndex < filteredSuggestions.length
      ? activeIndex
      : -1;

  const commitQuery = useCallback(() => {
    if (!query.trim()) return;
    onChange(serializeMarketTags(addMarketTag(selectedTags, query)));
    setQuery("");
  }, [onChange, query, selectedTags]);

  const closeSuggestions = useCallback(() => {
    commitQuery();
    setOpen(false);
    setActiveIndex(-1);
  }, [commitQuery]);

  useEffect(() => {
    if (!open) return;
    const closeOnOutsideClick = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        closeSuggestions();
      }
    };
    document.addEventListener("mousedown", closeOnOutsideClick);
    return () => document.removeEventListener("mousedown", closeOnOutsideClick);
  }, [closeSuggestions, open]);

  const openSuggestions = () => {
    setOpen(true);
    setActiveIndex(-1);
  };

  const chooseSuggestion = (tag: string) => {
    onChange(serializeMarketTags(addMarketTag(selectedTags, tag)));
    setQuery("");
    setOpen(true);
    setActiveIndex(-1);
  };

  const removeTag = (tag: string) => {
    onChange(serializeMarketTags(selectedTags.filter((item) => item !== tag)));
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      setQuery("");
      setOpen(false);
      setActiveIndex(-1);
      return;
    }
    if (event.key === "," || event.key === "，") {
      event.preventDefault();
      commitQuery();
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
    if (event.key === "Enter") {
      event.preventDefault();
      if (open && safeActiveIndex >= 0) chooseSuggestion(filteredSuggestions[safeActiveIndex]);
      else commitQuery();
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
        if (!rootRef.current?.contains(nextTarget)) closeSuggestions();
      }}
      ref={rootRef}
    >
      <div className={`${INPUT_CLASS} flex min-h-10 flex-wrap items-center gap-1.5 pr-9`}>
        {selectedTags.map((tag) => (
          <span
            className="inline-flex max-w-full items-center gap-1 rounded-md bg-[var(--theme-primary-light)] px-2 py-1 text-xs text-[var(--theme-primary)]"
            key={tag}
          >
            <span className="max-w-40 truncate">{tag}</span>
            <button
              aria-label={`移除标签 ${tag}`}
              className="rounded p-0.5 hover:bg-[var(--theme-primary)]/15 focus:outline-none focus-visible:ring-1 focus-visible:ring-[var(--theme-primary)]"
              disabled={disabled}
              onClick={() => removeTag(tag)}
              type="button"
            >
              <X aria-hidden="true" size={13} />
            </button>
          </span>
        ))}
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
          className="min-w-24 flex-1 bg-transparent py-0.5 text-sm outline-none"
          disabled={disabled}
          id={id}
          maxLength={80}
          onChange={(event) => {
            setQuery(event.target.value);
            setOpen(true);
            setActiveIndex(-1);
          }}
          onFocus={openSuggestions}
          onKeyDown={handleKeyDown}
          placeholder={selectedTags.length > 0 ? "继续添加标签" : "输入或选择标签，回车添加"}
          role="combobox"
          value={query}
        />
      </div>
      <ChevronDown
        aria-hidden="true"
        className={`pointer-events-none absolute right-3 top-5 -translate-y-1/2 text-[var(--theme-text-secondary)] transition-transform ${open ? "rotate-180" : ""}`}
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
              暂无匹配标签，可直接输入后按回车添加
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
              市场展示
            </h3>
            <p className="mt-1 text-sm leading-6 text-[var(--theme-text-secondary)]">
              配置员工在专家市场看到的统一说明、标签和逻辑头像。
            </p>
          </div>
        </div>
        <div className="mt-5 grid gap-5 lg:grid-cols-[minmax(0,1.15fr)_minmax(14rem,0.85fr)]">
          <div className="grid gap-4">
            <label className="flex flex-col gap-2">
              <span className="text-sm font-medium">专家说明</span>
              <textarea
                aria-label="专家说明"
                className={`${INPUT_CLASS} min-h-32 resize-y`}
                disabled={disabled}
                onChange={(event) => onChange({ description: event.target.value })}
                placeholder="说明这位专家适合解决什么问题"
                value={editor.description}
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
            <h4 className="text-sm font-semibold">对话启动问题</h4>
          </div>
          <ListField
            className="min-h-28"
            disabled={disabled}
            label="启动问题（可选）"
            onChange={(starterPrompts) => onChange({ starterPrompts })}
            values={editor.starterPrompts}
          />
        </div>
      </section>

      <section
        aria-label="访问范围（高级）"
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
            访问范围（高级）
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

        </details>
      </section>
    </>
  );
}
