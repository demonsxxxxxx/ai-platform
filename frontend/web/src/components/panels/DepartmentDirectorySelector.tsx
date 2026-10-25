import { Button, Input, Tag, SelectRoot, SelectTrigger, SelectPortal, SelectPositioner, SelectPopup, SelectList, SelectItem, SelectItemText, SelectItemIndicator } from "@lobehub/ui/base-ui";
import { useId, useMemo, useRef, useState } from "react";
import { AlertTriangle, Check, ChevronDown, Search, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { DepartmentDirectoryNode } from "../../services/api/capabilityDistribution";
import { flattenDepartmentDirectory, resolveDepartmentSelection } from "./departmentDirectorySelection";

const MAX_SELECTED_DEPARTMENTS = 128;

interface DepartmentDirectorySelectorProps {
  directory: DepartmentDirectoryNode[] | null;
  disabled?: boolean;
  loadError: string | null;
  onChange: (authorityIds: string[]) => void;
  selectedAuthorityIds: string[];
}

export function DepartmentDirectorySelector({
  directory,
  disabled = false,
  loadError,
  onChange,
  selectedAuthorityIds,
}: DepartmentDirectorySelectorProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const selectionStatusId = useId();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const optionRefs = useRef<Array<HTMLElement | null>>([]);
  const options = useMemo(() => directory ? flattenDepartmentDirectory(directory) : [], [directory]);
  const resolution = useMemo(() => resolveDepartmentSelection(selectedAuthorityIds, directory), [directory, selectedAuthorityIds]);
  const filtered = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    return normalized ? options.filter((option) => `${option.name} ${option.path}`.toLocaleLowerCase().includes(normalized)) : options;
  }, [options, query]);
  const remove = (id: string) => {
    if (disabled) return;
    onChange(selectedAuthorityIds.filter((item) => item !== id));
    triggerRef.current?.focus();
  };
  const focusOption = (direction: 1 | -1) => {
    const candidates = optionRefs.current.filter((item) => item && item.getAttribute("aria-disabled") !== "true");
    (direction === 1 ? candidates[0] : candidates.at(-1))?.focus();
  };
  return (
    <SelectRoot multiple value={selectedAuthorityIds} open={open} disabled={disabled || directory === null} onOpenChange={setOpen}
      onValueChange={(ids) => {
        if (!disabled && directory !== null && ids.length <= MAX_SELECTED_DEPARTMENTS) onChange(ids);
      }}
    >
      <div className="space-y-1.5" data-department-directory-selector data-skill-distribution-departments>
        <div className="flex flex-wrap items-center gap-1.5" data-department-selection-overflow>
          {resolution.resolved.map((option) => <Tag key={option.directoryId} className="max-w-full">
            <span className="max-w-60 truncate" title={option.path}>{option.name}</span>
            {!disabled ? <Button type="text" size="small" aria-label={t("skills.governance.departmentSelector.remove", { name: option.name })}
              title={t("skills.governance.departmentSelector.removeShort")} onClick={() => remove(option.authorityId)}><X size={13} /></Button> : null}
          </Tag>)}
          {resolution.unresolvedAuthorityIds.map((id) => <Tag key={id} color="warning" className="max-w-full" icon={<AlertTriangle size={13} />}>
            <span className="max-w-60 truncate" title={id}>{id}</span>
            {!disabled ? <Button type="text" size="small" aria-label={t("skills.governance.departmentSelector.removeUnresolved", { name: id })}
              title={t("skills.governance.departmentSelector.removeShort")} onClick={() => remove(id)}><X size={13} /></Button> : null}
          </Tag>)}
          {selectedAuthorityIds.length === 0 ? <span className="text-sm text-[var(--theme-text-secondary)]">{t("skills.governance.departments.all")}</span> : null}
          <SelectTrigger size="small" ref={triggerRef} style={{ width: 36 }} aria-describedby={selectionStatusId}
            aria-label={t("skills.governance.departmentSelector.selectAllowed")} title={t("skills.governance.departmentSelector.select")}
          ><Button type="default" size="small" icon={<ChevronDown size={16} />} /></SelectTrigger>
        </div>
        {loadError ? <p className="text-xs text-[var(--theme-danger-text)]" role="alert">{loadError}</p> : null}
        {!resolution.authoritative && selectedAuthorityIds.length > 0 ? <p id={selectionStatusId} className="text-xs text-[var(--theme-warning)]" role="status">{t("skills.governance.departmentSelector.unresolvedWarning")}</p> :
          selectedAuthorityIds.length >= MAX_SELECTED_DEPARTMENTS ? <p id={selectionStatusId} className="text-xs text-[var(--theme-text-secondary)]" role="status">{t("skills.governance.departmentSelector.limitReached")}</p> :
          <span className="sr-only" id={selectionStatusId}>{t("skills.governance.departmentSelector.selectionHelp")}</span>}
      </div>
      <SelectPortal>
        <SelectPositioner side="bottom" align="start" sideOffset={6}>
          <SelectPopup finalFocus={triggerRef} data-department-directory-popup style={{ minWidth: 280, maxWidth: "calc(100vw - 32px)" }} onKeyDown={(event) => { if (event.key === "Escape") { event.stopPropagation(); setOpen(false); } }}>
            <div className="p-2">
              <Input autoFocus prefix={<Search size={15} />} type="search" value={query} onChange={(event) => setQuery(event.target.value)}
                aria-label={t("skills.governance.departmentSelector.search")} placeholder={t("skills.governance.departmentSelector.searchPlaceholder")}
                onKeyDown={(event) => { if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); focusOption(event.key === "ArrowDown" ? 1 : -1); } }}
              />
            </div>
            <SelectList aria-label={t("skills.governance.departmentSelector.directory")} aria-describedby={selectionStatusId} className="max-h-64 overflow-y-auto">
              {filtered.map((option, index) => {
                const atLimit = !selectedAuthorityIds.includes(option.authorityId) && selectedAuthorityIds.length >= MAX_SELECTED_DEPARTMENTS;
                const reason = !option.selectable ? t("skills.governance.departmentSelector.duplicateReason") : atLimit ? t("skills.governance.departmentSelector.limitReason") : null;
                return <SelectItem key={option.directoryId} value={option.authorityId} disabled={disabled || !option.selectable || atLimit}
                  aria-label={reason ? `${option.path}，${reason}` : option.path} title={reason ?? option.path}
                  ref={(element) => { optionRefs.current[index] = element; }} style={{ paddingLeft: `${0.75 + option.depth * 1.1}rem` }}
                >
                  <SelectItemIndicator><Check size={13} /></SelectItemIndicator>
                  <SelectItemText>{option.name}</SelectItemText>
                  {!option.selectable ? <span className="text-[11px] text-[var(--theme-warning)]">{t("skills.governance.departmentSelector.duplicate")}</span> : null}
                </SelectItem>;
              })}
              {filtered.length === 0 ? <p className="p-4 text-center text-xs">{t("skills.governance.departmentSelector.empty")}</p> : null}
            </SelectList>
          </SelectPopup>
        </SelectPositioner>
      </SelectPortal>
    </SelectRoot>
  );
}
