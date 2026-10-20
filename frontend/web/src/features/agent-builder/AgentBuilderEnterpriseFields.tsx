import { AccordionRoot, AccordionItem, AccordionHeader, AccordionTrigger, AccordionPanel, AutoComplete, Button, Tag, TextArea } from "@lobehub/ui/base-ui";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Building2, MessageSquareText, ShieldCheck, X } from "lucide-react";

import { DepartmentDirectorySelector } from "../../components/panels/DepartmentDirectorySelector";
import {
  capabilityDistributionApi,
  type DepartmentDirectoryNode,
} from "../../services/api/capabilityDistribution";
import type { AgentBuilderEditor } from "./agentBuilderAdapter";
import { AgentAvatarPicker } from "./AgentAvatarPicker";
import { LobeSelectField } from "../../components/lobe-ui/LobeSelectField";

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

function MarketTagCombobox({ disabled, id, onChange, suggestions, value }: {
  disabled: boolean; id: string; onChange: (value: string) => void; suggestions: readonly string[]; value: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const queryRef = useRef("");
  const rootRef = useRef<HTMLDivElement>(null);
  const selectedTags = useMemo(() => parseMarketTags(value), [value]);
  const filteredSuggestions = useMemo(() => filterMarketTagSuggestions(suggestions, query, selectedTags), [suggestions, query, selectedTags]);
  const clearQuery = () => { queryRef.current = ""; setQuery(""); };
  const commitQuery = useCallback(() => {
    const pending = queryRef.current;
    if (!pending.trim()) return;
    queryRef.current = "";
    setQuery("");
    onChange(serializeMarketTags(addMarketTag(selectedTags, pending)));
  }, [onChange, selectedTags]);
  return (
    <div ref={rootRef} className="flex flex-wrap items-center gap-1.5"
      onBlur={(event) => { if (!rootRef.current?.contains(event.relatedTarget as Node | null)) { commitQuery(); setOpen(false); } }}
      onKeyDown={(event) => {
        if (event.nativeEvent.isComposing) return;
        if ((event.key === "Enter" && !event.defaultPrevented) || event.key === "," || event.key === "，") {
          event.preventDefault(); commitQuery();
        }
      }}
    >
      {selectedTags.map((tag) => <Tag key={tag} className="max-w-full">
        <span className="max-w-40 truncate">{tag}</span>
        <Button type="text" size="small" disabled={disabled} aria-label={`移除标签 ${tag}`}
          onClick={() => onChange(serializeMarketTags(selectedTags.filter((item) => item !== tag)))}><X size={13} /></Button>
      </Tag>)}
      <AutoComplete id={id} disabled={disabled} value={query} open={open} filter={null} options={filteredSuggestions}
        className="min-w-24 flex-1" placeholder="输入或选择标签" emptyText="暂无匹配标签，可直接输入后按回车添加"
        onChange={(next) => { queryRef.current = next; setQuery(next); }}
        onOpenChange={(next, details) => {
          setOpen(next);
          if (!next && details.reason === "escape-key") clearQuery();
          else if (!next && details.reason === "item-press") queueMicrotask(() => { commitQuery(); setOpen(true); });
          else if (!next && (details.reason === "outside-press" || details.reason === "focus-out")) commitQuery();
        }}
      />
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
      <TextArea
        aria-label={label}
        resize
        classNames={{ input: className ?? "min-h-24" }}
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
              <TextArea
                aria-label="专家说明"
                resize
                classNames={{ input: "min-h-32" }}
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
        <AccordionRoot variant="borderless" data-agent-builder-access-settings>
          <AccordionItem value="access">
          <AccordionHeader>
          <AccordionTrigger>
            <ShieldCheck
              aria-hidden="true"
              className="text-[var(--theme-text-secondary)]"
              size={17}
            />
            访问范围（高级）
          </AccordionTrigger>
          </AccordionHeader>
          <AccordionPanel keepMounted>
          <p className="mt-3 text-sm leading-6 text-[var(--theme-text-secondary)]">
            默认对公司内部用户开放；需要限制部门时再展开配置。
          </p>
        <div className="mt-4 flex max-w-sm flex-col gap-2">
          <span className="text-sm font-medium">可见范围</span>
          <LobeSelectField
            aria-label="可见范围"
            disabled={disabled}
            onChange={(visibility) =>
              onChange({ visibility: visibility as AgentBuilderEditor["visibility"] })
            }
            value={editor.visibility}
            options={[
              { value: "tenant", label: "全公司" },
              { value: "restricted", label: "指定部门" },
            ]}
          />
        </div>

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

          </div>
        ) : null}

          </AccordionPanel>
          </AccordionItem>
        </AccordionRoot>
      </section>
    </>
  );
}
