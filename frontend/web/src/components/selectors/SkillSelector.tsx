import { useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  Check,
  FileText,
  Search,
  Settings2,
  Sparkles,
  X,
} from "lucide-react";

import type { PublicSkillResponse } from "../../types";
import { skillMatchesQuery } from "../../utils/skillFilters";
import { useSwipeToClose } from "../../hooks/useSwipeToClose";

interface SkillSelectorProps {
  skills: PublicSkillResponse[];
  selectedSkill?: PublicSkillResponse | null;
  onSelectSkill: (skill: PublicSkillResponse) => void;
  isLoading?: boolean;
  isOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  searchSeed?: string;
}

function shortVersion(version: string): string {
  return version.slice(0, 8);
}

export function SkillSelector({
  skills,
  selectedSkill,
  onSelectSkill,
  isLoading = false,
  isOpen: externalIsOpen,
  onOpenChange: externalOnOpenChange,
  searchSeed,
}: SkillSelectorProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [internalOpen, setInternalOpen] = useState(false);
  const isOpen = externalIsOpen ?? internalOpen;
  const setIsOpen = externalOnOpenChange ?? setInternalOpen;
  const [searchQuery, setSearchQuery] = useState("");
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const titleId = useId();
  const swipeRef = useSwipeToClose({
    onClose: () => setIsOpen(false),
    enabled: isOpen,
  });

  useEffect(() => {
    if (!isOpen) return;
    const priorOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    requestAnimationFrame(() => searchInputRef.current?.focus());
    return () => {
      document.body.style.overflow = priorOverflow;
      if (!externalOnOpenChange) {
        requestAnimationFrame(() => triggerRef.current?.focus());
      }
    };
  }, [externalOnOpenChange, isOpen]);

  useEffect(() => {
    if (isOpen && searchSeed !== undefined) setSearchQuery(searchSeed);
  }, [isOpen, searchSeed]);

  const filteredSkills = useMemo(
    () => skills.filter((skill) => skillMatchesQuery(skill, searchQuery)),
    [searchQuery, skills],
  );

  const handleDialogKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      setIsOpen(false);
      return;
    }
    if (event.key !== "Tab") return;

    const focusable = Array.from(
      dialogRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
      ) ?? [],
    ).filter((element) => element.getClientRects().length > 0);
    if (focusable.length === 0) {
      event.preventDefault();
      dialogRef.current?.focus();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const modal = (
    <div
      ref={(node) => {
        dialogRef.current = node;
        swipeRef.current = node;
      }}
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      tabIndex={-1}
      className="flex min-h-[40vh] max-h-[85dvh] w-full flex-col overflow-hidden rounded-t-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] shadow-[0_8px_24px_rgba(18,38,63,0.12)] sm:min-h-0 sm:w-[min(640px,calc(100vw-2rem))] sm:max-h-[80vh] sm:rounded-lg"
      data-composer-skill-selector
      onClick={(event) => event.stopPropagation()}
      onKeyDown={handleDialogKeyDown}
    >
      <div className="relative flex items-center justify-between border-b border-[var(--theme-border)] px-4 py-3 sm:px-5 sm:py-4">
        <div className="absolute left-1/2 top-2 h-1 w-10 -translate-x-1/2 rounded-full bg-[var(--theme-border)] sm:hidden" />
        <div className="mt-2 flex min-w-0 items-center gap-3 sm:mt-0">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-[var(--theme-bg-sidebar)] ring-1 ring-[var(--theme-border)]">
            <Sparkles size={17} className="text-[var(--theme-text-secondary)]" />
          </div>
          <div className="min-w-0">
            <h2
              id={titleId}
              className="truncate text-sm font-semibold text-[var(--theme-text)] sm:text-base"
            >
              {t("skillSelector.taskTitle", "Choose a Skill")}
            </h2>
            <p className="truncate text-xs text-[var(--theme-text-secondary)]">
              {selectedSkill
                ? t("skillSelector.taskSelected", "One Skill selected for this task")
                : t("skillSelector.taskOptional", "Optional for this task")}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => setIsOpen(false)}
          className="inline-flex size-11 items-center justify-center rounded-lg text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-bg-sidebar)] hover:text-[var(--theme-text)] sm:size-9"
          aria-label={t("common.close", "Close")}
        >
          <X size={18} />
        </button>
      </div>

      <div className="border-b border-[var(--theme-border)] bg-[var(--theme-bg)] px-4 py-3 sm:px-5">
        <div className="relative">
          <Search
            size={16}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--theme-text-secondary)]"
          />
          <input
            ref={searchInputRef}
            type="search"
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
            placeholder={t("skills.searchPlaceholder", "Search Skills")}
            className="panel-search h-10"
            data-composer-skill-search
          />
        </div>
      </div>

      <div className="flex-1 space-y-1 overflow-y-auto p-2.5 sm:p-3">
        {isLoading ? (
          <div className="px-3 py-8 text-center text-sm text-[var(--theme-text-secondary)]">
            {t("common.loading", "Loading...")}
          </div>
        ) : (
          filteredSkills.map((skill) => {
            const selected =
              selectedSkill?.name === skill.name &&
              selectedSkill?.expected_version === skill.expected_version;
            return (
              <button
                key={skill.name}
                type="button"
                onClick={() => {
                  onSelectSkill(skill);
                  setIsOpen(false);
                }}
                className={`flex w-full items-start gap-3 rounded-lg border px-3 py-3 text-left transition-colors ${
                  selected
                    ? "border-[var(--theme-info-ring)] bg-[var(--theme-info-soft)]"
                    : "border-transparent hover:border-[var(--theme-border)] hover:bg-[var(--theme-bg-sidebar)]"
                }`}
                data-composer-skill-row={skill.name}
                data-composer-skill-state={selected ? "selected" : "available"}
                aria-pressed={selected}
              >
                <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-[var(--theme-bg-sidebar)] ring-1 ring-[var(--theme-border)]">
                  <Sparkles size={15} className="text-[var(--theme-text-secondary)]" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="min-w-0 truncate text-sm font-medium text-[var(--theme-text)]">
                      {skill.name}
                    </span>
                    <span
                      className="font-mono text-xs text-[var(--theme-text-secondary)]"
                      data-composer-skill-version={skill.expected_version}
                    >
                      v{shortVersion(skill.expected_version)}
                    </span>
                    {skill.requires_file && (
                      <span
                        className="inline-flex items-center gap-1 text-xs text-[var(--theme-warning)]"
                        data-composer-skill-requires-file
                      >
                        <FileText size={12} />
                        {t("skillSelector.fileRequired", "File required")}
                      </span>
                    )}
                  </div>
                  <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-[var(--theme-text-secondary)]">
                    {skill.description || t("skillSelector.noDescription")}
                  </p>
                </div>
                <span className="flex size-6 shrink-0 items-center justify-center text-[var(--theme-primary)]">
                  {selected && <Check size={17} />}
                </span>
              </button>
            );
          })
        )}
        {!isLoading && filteredSkills.length === 0 && (
          <div className="rounded-lg border border-dashed border-[var(--theme-border)] px-4 py-8 text-center text-sm text-[var(--theme-text-secondary)]">
            {t("skills.noMatchingSkills", "No matching Skills")}
          </div>
        )}
      </div>

      <div className="flex items-center gap-2 border-t border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:px-5">
        <span className="text-xs text-[var(--theme-text-secondary)]">
          {t("skillSelector.singleSelectionHint", "Only one Skill can run this task")}
        </span>
        <span className="flex-1" />
        <button
          type="button"
          onClick={() => {
            setIsOpen(false);
            navigate("/skills");
          }}
          className="inline-flex h-11 items-center gap-1.5 rounded-lg px-2 text-xs font-medium text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-workbench-panel)] hover:text-[var(--theme-text)] sm:h-9"
        >
          <Settings2 size={14} />
          {t("skillSelector.manage", "Manage")}
        </button>
        <button
          type="button"
          onClick={() => setIsOpen(false)}
          className="h-11 rounded-lg bg-[var(--theme-primary)] px-3 text-xs font-medium text-[var(--theme-primary-foreground)] transition-colors hover:bg-[var(--theme-primary-hover)] sm:h-9"
        >
          {t("skillSelector.done", "Done")}
        </button>
      </div>
    </div>
  );

  if (externalOnOpenChange) {
    return isOpen
      ? createPortal(
          <>
            <div
              data-yields-sidebar
              className="fixed inset-0 z-[300] bg-[var(--theme-overlay)] animate-fade-in"
              onClick={() => setIsOpen(false)}
            />
            <div
              className="fixed inset-x-0 bottom-0 z-[301] animate-slide-up sm:inset-0 sm:flex sm:items-center sm:justify-center sm:p-4 sm:animate-scale-in"
              onClick={() => setIsOpen(false)}
            >
              {modal}
            </div>
          </>,
          document.body,
        )
      : null;
  }

  return (
    <button
      ref={triggerRef}
      type="button"
      onClick={() => setIsOpen(true)}
      className="chat-tool-btn"
      aria-label={t("skillSelector.taskTitle", "Choose a Skill")}
    >
      <Sparkles size={18} />
    </button>
  );
}
