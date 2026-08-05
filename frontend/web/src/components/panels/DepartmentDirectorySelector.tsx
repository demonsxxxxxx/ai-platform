import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { AlertTriangle, Check, ChevronDown, Search, X } from "lucide-react";

import type { DepartmentDirectoryNode } from "../../services/api/capabilityDistribution";
import {
  flattenDepartmentDirectory,
  resolveDepartmentSelection,
} from "./departmentDirectorySelection";

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
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const listboxId = useId();
  const selectionStatusId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const options = useMemo(
    () => (directory ? flattenDepartmentDirectory(directory) : []),
    [directory],
  );
  const resolution = useMemo(
    () => resolveDepartmentSelection(selectedAuthorityIds, directory),
    [directory, selectedAuthorityIds],
  );
  const filtered = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    if (!normalized) return options;
    return options.filter((option) =>
      `${option.name} ${option.path}`.toLocaleLowerCase().includes(normalized),
    );
  }, [options, query]);

  useEffect(() => {
    if (!open) return;
    const closeOnOutsideClick = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", closeOnOutsideClick);
    return () => document.removeEventListener("mousedown", closeOnOutsideClick);
  }, [open]);

  useEffect(() => {
    if (open) searchRef.current?.focus();
  }, [open]);

  const closeAndRestoreFocus = () => {
    setOpen(false);
    triggerRef.current?.focus();
  };

  const focusOption = (start: number, direction: 1 | -1) => {
    if (filtered.length === 0) return;
    for (let offset = 0; offset < filtered.length; offset += 1) {
      const index = (start + offset * direction + filtered.length) % filtered.length;
      const option = optionRefs.current[index];
      if (option && !option.disabled) {
        option.focus();
        return;
      }
    }
  };

  const onSearchKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closeAndRestoreFocus();
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      focusOption(0, 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      focusOption(filtered.length - 1, -1);
    }
  };

  const onOptionKeyDown = (
    event: KeyboardEvent<HTMLButtonElement>,
    index: number,
  ) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closeAndRestoreFocus();
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      focusOption(index + 1, 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      focusOption(index - 1, -1);
    } else if (event.key === "Home") {
      event.preventDefault();
      focusOption(0, 1);
    } else if (event.key === "End") {
      event.preventDefault();
      focusOption(filtered.length - 1, -1);
    }
  };

  const remove = (authorityId: string) => {
    onChange(selectedAuthorityIds.filter((item) => item !== authorityId));
    triggerRef.current?.focus();
  };

  const toggle = (authorityId: string) => {
    if (
      !selectedAuthorityIds.includes(authorityId) &&
      selectedAuthorityIds.length >= MAX_SELECTED_DEPARTMENTS
    ) {
      return;
    }
    onChange(
      selectedAuthorityIds.includes(authorityId)
        ? selectedAuthorityIds.filter((item) => item !== authorityId)
        : [...selectedAuthorityIds, authorityId],
    );
  };

  return (
    <div
      className="department-selector"
      data-department-directory-selector
      data-skill-distribution-departments
      ref={rootRef}
    >
      <div
        className="department-selector__selection"
        data-department-selection-overflow
      >
        {resolution.resolved.map((option) => (
          <span className="department-selector__chip" key={option.directoryId}>
            <span className="truncate" title={option.path}>
              {option.name}
            </span>
            {!disabled ? (
              <button
                aria-label={`移除 ${option.name}`}
                onClick={() => remove(option.authorityId)}
                title="移除"
                type="button"
              >
                <X aria-hidden="true" size={13} />
              </button>
            ) : null}
          </span>
        ))}
        {resolution.unresolvedAuthorityIds.map((authorityId) => (
          <span
            className="department-selector__chip department-selector__chip--unresolved"
            key={authorityId}
          >
            <AlertTriangle aria-hidden="true" size={13} />
            <span className="truncate" title={authorityId}>
              {authorityId}
            </span>
            {!disabled ? (
              <button
                aria-label={`移除未确认部门 ${authorityId}`}
                onClick={() => remove(authorityId)}
                title="移除"
                type="button"
              >
                <X aria-hidden="true" size={13} />
              </button>
            ) : null}
          </span>
        ))}
        {selectedAuthorityIds.length === 0 ? (
          <span className="department-selector__placeholder">全部部门</span>
        ) : null}
        <button
          aria-controls={listboxId}
          aria-describedby={selectionStatusId}
          aria-expanded={open}
          aria-haspopup="listbox"
          aria-label="选择允许部门"
          className="department-selector__trigger"
          disabled={disabled || directory === null}
          onClick={() => setOpen((current) => !current)}
          onKeyDown={(event) => {
            if (!open && ["ArrowDown", "Enter", " "].includes(event.key)) {
              event.preventDefault();
              setOpen(true);
            }
          }}
          ref={triggerRef}
          title="选择部门"
          type="button"
        >
          <ChevronDown
            aria-hidden="true"
            className={open ? "rotate-180" : undefined}
            size={16}
          />
        </button>
      </div>

      {open ? (
        <div className="department-selector__menu">
          <div className="department-selector__search">
            <Search aria-hidden="true" size={15} />
            <input
              aria-label="搜索部门"
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={onSearchKeyDown}
              placeholder="搜索部门名称或路径"
              ref={searchRef}
              type="search"
              value={query}
            />
          </div>
          <div
            aria-label="权威部门目录"
            aria-describedby={selectionStatusId}
            aria-multiselectable="true"
            className="department-selector__options"
            id={listboxId}
            role="listbox"
          >
            {filtered.map((option, index) => {
              const checked = selectedAuthorityIds.includes(option.authorityId);
              const atSelectionLimit =
                !checked && selectedAuthorityIds.length >= MAX_SELECTED_DEPARTMENTS;
              const unavailable = !option.selectable || atSelectionLimit;
              const unavailableReason = !option.selectable
                ? "名称重复，不能作为分发权威"
                : atSelectionLimit
                  ? "已达到 128 个部门的选择上限"
                  : null;
              return (
                <button
                  aria-disabled={unavailable}
                  aria-label={
                    unavailableReason
                      ? `${option.path}，${unavailableReason}`
                      : option.path
                  }
                  aria-selected={checked}
                  className="department-selector__option"
                  key={option.directoryId}
                  onClick={() => {
                    if (!unavailable) toggle(option.authorityId);
                  }}
                  onKeyDown={(event) => onOptionKeyDown(event, index)}
                  ref={(element) => {
                    optionRefs.current[index] = element;
                  }}
                  role="option"
                  style={{ paddingLeft: `${0.75 + option.depth * 1.1}rem` }}
                  title={
                    !option.selectable
                      ? "名称重复，不能作为分发权威"
                      : atSelectionLimit
                        ? "最多选择 128 个部门"
                        : option.path
                  }
                  type="button"
                >
                  <span aria-hidden="true" className="department-selector__check">
                    {checked ? <Check size={13} /> : null}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-left">
                    {option.name}
                  </span>
                  {!option.selectable ? (
                    <span className="text-[11px] text-[var(--theme-warning)]">
                      名称重复
                    </span>
                  ) : null}
                </button>
              );
            })}
            {filtered.length === 0 ? (
              <p className="px-3 py-4 text-center text-xs text-[var(--theme-text-secondary)]">
                没有匹配的部门
              </p>
            ) : null}
          </div>
        </div>
      ) : null}

      {loadError ? (
        <p className="mt-1.5 text-xs text-[var(--theme-danger)]" role="alert">
          {loadError}
        </p>
      ) : null}
      {!resolution.authoritative && selectedAuthorityIds.length > 0 ? (
        <p
          className="mt-1.5 text-xs text-[var(--theme-warning)]"
          id={selectionStatusId}
          role="status"
        >
          未确认部门会保留显示；请移除它们，或等待权威目录恢复后再保存。
        </p>
      ) : selectedAuthorityIds.length >= MAX_SELECTED_DEPARTMENTS ? (
        <p
          className="mt-1.5 text-xs text-[var(--theme-text-secondary)]"
          id={selectionStatusId}
          role="status"
        >
          已达到 128 个部门的选择上限。
        </p>
      ) : (
        <span className="sr-only" id={selectionStatusId}>
          可从权威部门目录中选择最多 128 个部门。
        </span>
      )}
    </div>
  );
}
