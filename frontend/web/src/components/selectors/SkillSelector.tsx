import { Button, Input, ModalRoot, ModalPortal, ModalBackdrop, ModalPopup, ModalTitle } from "@lobehub/ui/base-ui";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  ArrowRight,
  Check,
  FileText,
  Search,
  Sparkles,
  X,
} from "lucide-react";

import type { PublicSkillResponse } from "../../types";
import { skillMatchesQuery } from "../../utils/skillFilters";
import { useAuth } from "../../hooks/useAuth";
import { useSwipeToClose } from "../../hooks/useSwipeToClose";
import { isAiAdminUser } from "../panels/capabilityAdmin";

interface SkillSelectorProps {
  skills: PublicSkillResponse[];
  selectedSkill?: PublicSkillResponse | null;
  onSelectSkill: (skill: PublicSkillResponse) => void;
  isLoading?: boolean;
  isOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  searchSeed?: string;
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
  const { user } = useAuth();
  const navigate = useNavigate();
  const [internalOpen, setInternalOpen] = useState(false);
  const isOpen = externalIsOpen ?? internalOpen;
  const setIsOpen = externalOnOpenChange ?? setInternalOpen;
  const [searchQuery, setSearchQuery] = useState("");
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const titleId = useId();
  const swipeRef = useSwipeToClose({
    onClose: () => setIsOpen(false),
    enabled: isOpen,
  });

  useEffect(() => {
    if (isOpen && searchSeed !== undefined) setSearchQuery(searchSeed);
  }, [isOpen, searchSeed]);

  const filteredSkills = useMemo(
    () => skills.filter((skill) => skillMatchesQuery(skill, searchQuery)),
    [searchQuery, skills],
  );
  const selectionIsCurrent = useMemo(
    () =>
      Boolean(
        selectedSkill &&
          skills.some(
            (skill) =>
              skill.name === selectedSkill.name &&
              skill.expected_version === selectedSkill.expected_version,
          ),
      ),
    [selectedSkill, skills],
  );
  const selectionSummary = !selectedSkill
    ? "optional"
    : selectionIsCurrent
      ? "selected"
      : "reconfirm";
  const skillsLinkLabel = isAiAdminUser(user)
    ? t("nav.skillManagement")
    : t("skillSelector.viewSkills", "View Skills");

  const modal = (
    <div
      ref={(node) => { swipeRef.current = node; }}
      className="flex min-h-[40vh] max-h-[85dvh] w-full flex-col overflow-hidden sm:min-h-0 sm:max-h-[80vh]"
      data-composer-skill-selector
    >
      <div className="relative flex items-center justify-between border-b border-[var(--theme-border)] px-4 py-3 sm:px-5 sm:py-4">
        <div className="absolute left-1/2 top-2 h-1 w-10 -translate-x-1/2 rounded-full bg-[var(--theme-border)] sm:hidden" />
        <div className="mt-2 flex min-w-0 items-center gap-3 sm:mt-0">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-[var(--theme-bg-sidebar)] ring-1 ring-[var(--theme-border)]">
            <Sparkles size={17} className="text-[var(--theme-text-secondary)]" />
          </div>
          <div className="min-w-0">
            <ModalTitle id={titleId}>
              {t("skillSelector.taskTitle", "Choose a Skill")}
            </ModalTitle>
            <p
              className="truncate text-xs text-[var(--theme-text-secondary)]"
              data-composer-skill-selection-summary={selectionSummary}
            >
              {selectionSummary === "selected"
                ? t("skillSelector.taskSelected", "One Skill selected for this task")
                : selectionSummary === "reconfirm"
                  ? t(
                      "skillSelector.taskReconfirm",
                      "This Skill was updated. Select it again.",
                    )
                  : t("skillSelector.taskOptional", "Optional for this task")}
            </p>
          </div>
        </div>
        <Button type="text"
          htmlType="button"
          onClick={() => setIsOpen(false)}
          size="large" icon={<X size={18} />}
          aria-label={t("common.close", "Close")}
        />
      </div>

      <div className="border-b border-[var(--theme-border)] bg-[var(--theme-bg)] px-4 py-3 sm:px-5">
        <div>
          <Input
            prefix={<Search size={16} />}
            ref={searchInputRef}
            type="search"
            aria-label={t("skills.searchPlaceholder", "Search Skills")}
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
            placeholder={t("skills.searchPlaceholder", "Search Skills")}
            size="large" className="w-full"
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
              <Button type="text"
                key={skill.name}
                style={{ height: "auto", padding: 12, alignItems: "flex-start" }}
                htmlType="button"
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
              </Button>
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
        <Button type="text"
          htmlType="button"
          onClick={() => {
            setIsOpen(false);
            navigate("/skills");
          }}
          className="inline-flex h-11 items-center gap-1.5 rounded-lg px-2 text-xs font-medium text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-workbench-panel)] hover:text-[var(--theme-text)] sm:h-9"
        >
          <ArrowRight size={14} />
          {skillsLinkLabel}
        </Button>
        <Button type="primary"
          htmlType="button"
          onClick={() => setIsOpen(false)}
        >
          {t("skillSelector.done", "Done")}
        </Button>
      </div>
    </div>
  );

  return (
    <>
      {!externalOnOpenChange ? <Button type="text" size="large" ref={triggerRef} onClick={() => setIsOpen(true)}
        aria-label={t("skillSelector.taskTitle", "Choose a Skill")} icon={<Sparkles size={18} />} /> : null}
      <ModalRoot open={isOpen} onOpenChange={setIsOpen}>
        <ModalPortal>
          <ModalBackdrop />
          <ModalPopup width="min(40rem, 100vw)" className="!items-end sm:!items-center"
            aria-labelledby={titleId} initialFocus={searchInputRef} finalFocus={externalOnOpenChange ? true : triggerRef}
          >{modal}</ModalPopup>
        </ModalPortal>
      </ModalRoot>
    </>
  );
}
