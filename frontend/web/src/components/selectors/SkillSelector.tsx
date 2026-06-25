import { useState, useEffect, useMemo } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import toast from "react-hot-toast";
import {
  Sparkles,
  ChevronRight,
  X,
  Plus,
  FileCode,
  Store,
  Search,
  Tag,
} from "lucide-react";
import { Checkbox } from "../common/Checkbox";
import type { SkillResponse, SkillSource } from "../../types";
import { collectSkillTags, skillMatchesQuery } from "../../utils/skillFilters";
import { useSwipeToClose } from "../../hooks/useSwipeToClose";

interface SkillSelectorProps {
  skills: SkillResponse[];
  onToggleSkill: (name: string) => Promise<boolean>;
  onToggleCategory: (
    category: SkillSource,
    enabled: boolean,
  ) => Promise<boolean>;
  onToggleAll: (enabled: boolean) => Promise<boolean>;
  pendingSkillNames?: string[];
  isMutating?: boolean;
  enabledCount: number;
  totalCount: number;
  controlledByPersonaName?: string | null;
  isOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  searchSeed?: string;
}

const sourceIcons: Record<SkillSource, typeof FileCode> = {
  marketplace: Store,
  manual: FileCode,
};

const sourceColors: Record<SkillSource, string> = {
  marketplace: "text-[var(--theme-primary)]",
  manual: "text-[var(--theme-text)]",
};

export function SkillSelector({
  skills,
  onToggleSkill,
  onToggleCategory,
  onToggleAll,
  pendingSkillNames = [],
  isMutating = false,
  enabledCount,
  totalCount,
  controlledByPersonaName,
  isOpen: externalIsOpen,
  onOpenChange: externalOnOpenChange,
  searchSeed,
}: SkillSelectorProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [internalOpen, setInternalOpen] = useState(false);
  const isOpen = externalIsOpen ?? internalOpen;
  const setIsOpen = externalOnOpenChange ?? setInternalOpen;
  const [expandedCategories, setExpandedCategories] = useState<
    Set<SkillSource>
  >(new Set(["marketplace", "manual"]));
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const swipeRef = useSwipeToClose({
    onClose: () => setIsOpen(false),
    enabled: isOpen,
  });

  // 锁定滚动
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen || searchSeed === undefined) return;
    setSearchQuery(searchSeed);
    setSelectedTags([]);
  }, [isOpen, searchSeed]);

  // 按来源分组 - 使用 useMemo 缓存计算结果
  const filteredSkills = useMemo(
    () =>
      skills.filter((skill) => {
        const matchesQuery = skillMatchesQuery(skill, searchQuery);
        const matchesTags =
          selectedTags.length === 0 ||
          selectedTags.every((tag) => skill.tags.includes(tag));

        return matchesQuery && matchesTags;
      }),
    [searchQuery, selectedTags, skills],
  );

  const groupedSkills = useMemo(
    () =>
      filteredSkills.reduce(
        (acc, skill) => {
          if (!acc[skill.source]) {
            acc[skill.source] = [];
          }
          acc[skill.source].push(skill);
          return acc;
        },
        {} as Record<SkillSource, SkillResponse[]>,
      ),
    [filteredSkills],
  );

  const availableTags = useMemo(() => collectSkillTags(skills), [skills]);
  const hasActiveFilters =
    searchQuery.trim().length > 0 || selectedTags.length > 0;
  const pendingSet = useMemo(
    () => new Set(pendingSkillNames),
    [pendingSkillNames],
  );
  const allSkillsEnabled = totalCount > 0 && enabledCount === totalCount;
  const noSkillsEnabled = enabledCount === 0;
  const personaControlled = !!controlledByPersonaName;

  const showBatchToggleToast = (
    enabled: boolean,
    count: number,
    ok: boolean,
  ) => {
    if (ok) {
      toast.success(
        enabled
          ? t("skills.batchEnableSuccess", { count })
          : t("skills.batchDisableSuccess", { count }),
      );
      return;
    }
    toast.error(t("skills.batchToggleFailed"));
  };

  const showSingleToggleToast = (enabled: boolean, ok: boolean) => {
    if (ok) {
      toast.success(
        enabled
          ? t("skills.batchEnableSuccess", { count: 1 })
          : t("skills.batchDisableSuccess", { count: 1 }),
      );
      return;
    }
    toast.error(t("skills.batchToggleFailed"));
  };

  const toggleCategoryExpand = (source: SkillSource) => {
    setExpandedCategories((prev) => {
      const newSet = new Set(prev);
      if (newSet.has(source)) {
        newSet.delete(source);
      } else {
        newSet.add(source);
      }
      return newSet;
    });
  };

  const toggleTag = (tag: string) => {
    setSelectedTags((prev) =>
      prev.includes(tag) ? prev.filter((item) => item !== tag) : [...prev, tag],
    );
  };

  const ModalContent = () => (
    <div
      ref={swipeRef as React.RefObject<HTMLDivElement>}
      className="w-full min-h-[40vh] max-h-[85vh] max-h-[85dvh] flex flex-col overflow-hidden rounded-t-lg border border-[var(--theme-border)] shadow-[0_8px_24px_rgba(18,38,63,0.12)] sm:w-[40%] sm:min-w-[600px] sm:max-h-[80vh] sm:rounded-lg"
      style={{ background: "var(--theme-bg-card)" }}
      onClick={(e) => e.stopPropagation()}
    >
      {/* Header */}
      <div
        className="flex items-center justify-between px-4 sm:px-5 py-3 sm:py-4 border-b"
        style={{ borderColor: "var(--theme-border)" }}
      >
        {/* Mobile drag handle */}
        <div className="absolute left-1/2 -translate-x-1/2 top-2 w-10 h-1 rounded-full bg-[var(--theme-border)] sm:hidden" />
        <div className="flex items-center gap-3 mt-2 sm:mt-0">
          <div className="size-9 sm:size-10 rounded-lg bg-[var(--theme-bg-sidebar)] ring-1 ring-[var(--theme-border)] flex items-center justify-center">
            <Sparkles
              size={16}
              className="text-[var(--theme-text-secondary)] sm:w-[18px] sm:h-[18px]"
            />
          </div>
          <div>
            <h2 className="text-sm sm:text-base font-semibold text-[var(--theme-text)]">
              {t("skillSelector.title")}
            </h2>
            <p className="text-xs sm:text-xs text-[var(--theme-text-secondary)]">
              {t("skillSelector.selected", {
                enabled: enabledCount,
                total: totalCount,
              })}
            </p>
          </div>
        </div>
        <button
          onClick={() => setIsOpen(false)}
          className="p-2 rounded-lg text-[var(--theme-text-secondary)] hover:bg-[var(--theme-bg-sidebar)] hover:text-[var(--theme-text)] active:bg-[var(--theme-bg-sidebar)] transition-colors"
        >
          <X size={18} />
        </button>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-2 px-4 sm:px-5 py-2 sm:py-2.5 border-b border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)]">
        <button
          onClick={async () => {
            const changedCount = totalCount - enabledCount;
            if (changedCount === 0) {
              return;
            }
            const ok = await onToggleAll(true);
            showBatchToggleToast(true, changedCount, ok);
          }}
          disabled={personaControlled || isMutating || allSkillsEnabled}
          className="px-3 py-2 sm:py-1.5 text-xs font-medium text-[var(--theme-text-secondary)] hover:text-[var(--theme-text)] hover:bg-[var(--theme-bg-card)] active:bg-[var(--theme-bg-card)] rounded-lg transition-colors disabled:cursor-not-allowed disabled:opacity-50"
        >
          {t("skillSelector.selectAll")}
        </button>
        <div className="w-px h-4 bg-[var(--theme-border)]" />
        <button
          onClick={async () => {
            if (enabledCount === 0) {
              return;
            }
            const ok = await onToggleAll(false);
            showBatchToggleToast(false, enabledCount, ok);
          }}
          disabled={personaControlled || isMutating || noSkillsEnabled}
          className="px-3 py-2 sm:py-1.5 text-xs font-medium text-[var(--theme-text-secondary)] hover:text-[var(--theme-text)] hover:bg-[var(--theme-bg-card)] active:bg-[var(--theme-bg-card)] rounded-lg transition-colors disabled:cursor-not-allowed disabled:opacity-50"
        >
          {t("skillSelector.deselectAll")}
        </button>
        <div className="flex-1" />
        <button
          type="button"
          onClick={() => {
            setIsOpen(false);
            navigate("/skills");
          }}
          className="flex items-center gap-1 px-3 py-2 sm:py-1.5 text-xs font-medium text-[var(--theme-text-secondary)] hover:text-[var(--theme-text)] hover:bg-[var(--theme-bg-sidebar)] rounded-lg transition-colors"
        >
          <Plus size={14} />
          <span>{t("skillSelector.manage")}</span>
        </button>
      </div>

      {personaControlled && (
        <div className="border-b border-blue-200/70 bg-blue-50/80 px-4 py-3 text-xs leading-relaxed text-blue-700 dark:border-blue-500/20 dark:bg-blue-500/10 dark:text-blue-200 sm:px-5">
          {t(
            "personaPresets.skillsControlledHint",
            "当前角色「{{name}}」正在控制可用 Skills。要调整本次对话的技能，请先清除当前角色，或编辑该角色预设。",
            { name: controlledByPersonaName },
          )}
        </div>
      )}

      <div className="border-b border-[var(--theme-border)] bg-[var(--theme-bg)] px-4 py-3 sm:px-5">
        <div className="relative">
          <Search
            size={16}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--theme-text-secondary)]"
          />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder={t("skills.searchPlaceholder")}
            className="panel-search h-10"
          />
        </div>
        {availableTags.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {availableTags.map((tag) => (
              <button
                key={tag}
                type="button"
                onClick={() => toggleTag(tag)}
                className={`skill-tag-chip ${
                  selectedTags.includes(tag) ? "skill-tag-chip--active" : ""
                }`}
              >
                <Tag size={11} />
                {tag}
              </button>
            ))}
            {hasActiveFilters && (
              <button
                type="button"
                onClick={() => {
                  setSearchQuery("");
                  setSelectedTags([]);
                }}
                className="text-xs text-[var(--theme-text-secondary)] transition-colors hover:text-[var(--theme-primary)]"
              >
                {t("marketplace.clearFilters")}
              </button>
            )}
          </div>
        )}
      </div>

      {/* Categories */}
      <div className="flex-1 overflow-y-auto p-2.5 sm:p-3 space-y-1.5">
        {Object.entries(groupedSkills).map(
          ([source, categorySkills]: [string, SkillResponse[]]) => {
            const cat = source as SkillSource;
            const Icon = sourceIcons[cat];
            const enabledInCategory = categorySkills.filter(
              (s: SkillResponse) => s.enabled,
            ).length;
            const allEnabled = enabledInCategory === categorySkills.length;
            const isExpanded = expandedCategories.has(cat);
            const categoryPending = categorySkills.some((skill) =>
              pendingSet.has(skill.name),
            );
            const categoryTargetEnabled = !allEnabled;
            const categoryChangedCount = categorySkills.filter(
              (skill) => skill.enabled !== categoryTargetEnabled,
            ).length;

            return (
              <div
                key={source}
                className="rounded-lg border border-[var(--theme-border)] overflow-hidden bg-[var(--theme-bg-card)]"
              >
                {/* Category Header */}
                <div
                  className="flex items-center gap-2 sm:gap-2.5 px-3 sm:px-3.5 py-2.5 cursor-pointer hover:bg-[var(--theme-bg-sidebar)] active:bg-[var(--theme-bg-sidebar)] transition-all duration-200"
                  onClick={() => toggleCategoryExpand(cat)}
                >
                  <ChevronRight
                    size={16}
                    className={`text-[var(--theme-text-secondary)] transition-transform duration-200 ease-out ${
                      isExpanded ? "rotate-90" : ""
                    }`}
                  />
                  <div className="w-6 h-6 sm:w-7 sm:h-7 rounded-lg bg-[var(--theme-bg-sidebar)] flex items-center justify-center ring-1 ring-[var(--theme-border)]">
                    <Icon
                      size={13}
                      className={`${sourceColors[cat]} sm:w-[14px] sm:h-[14px]`}
                    />
                  </div>
                  <div className="flex-1 min-w-0">
                    <span className="text-[13px] sm:text-sm font-medium text-[var(--theme-text)]">
                      {t(`skillSelector.sources.${cat}`)}
                    </span>
                    <span className="ml-1.5 sm:ml-2 text-xs sm:text-xs text-[var(--theme-text-secondary)] tabular-nums">
                      {enabledInCategory}/{categorySkills.length}
                    </span>
                  </div>
                  <Checkbox
                    checked={allEnabled}
                    pending={categoryPending}
                    disabled={
                      personaControlled ||
                      isMutating ||
                      categoryChangedCount === 0
                    }
                    onChange={async () => {
                      if (
                        personaControlled ||
                        isMutating ||
                        categoryChangedCount === 0
                      )
                        return;
                      const ok = await onToggleCategory(
                        cat,
                        categoryTargetEnabled,
                      );
                      showBatchToggleToast(
                        categoryTargetEnabled,
                        categoryChangedCount,
                        ok,
                      );
                    }}
                  />
                </div>

                {/* Skills List */}
                {isExpanded && (
                  <div className="animate-[fade-in_150ms_ease-out]">
                    <div className="px-1 sm:px-2 pb-2 pt-1 space-y-0.5">
                      {categorySkills.map((skill: SkillResponse) => (
                        <div key={skill.name} className="group">
                          {/* Skill Row */}
                          <button
                            type="button"
                            disabled={personaControlled || isMutating}
                            className={`flex w-full items-center gap-1.5 sm:gap-2 px-2 sm:px-2.5 py-2 sm:py-2 rounded-lg transition-all duration-200 disabled:cursor-not-allowed ${
                              skill.enabled
                                ? "hover:bg-[var(--theme-bg-sidebar)] active:bg-[var(--theme-bg-sidebar)]"
                                : "bg-[var(--theme-primary)]/[0.06] hover:bg-[var(--theme-primary)]/[0.12] active:bg-[var(--theme-primary)]/[0.18]"
                            } ${
                              pendingSet.has(skill.name) ||
                              personaControlled ||
                              isMutating
                                ? "opacity-70"
                                : ""
                            }`}
                            onClick={async () => {
                              if (personaControlled || isMutating) {
                                return;
                              }
                              const ok = await onToggleSkill(skill.name);
                              showSingleToggleToast(!skill.enabled, ok);
                            }}
                          >
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-1.5 sm:gap-2 flex-wrap">
                                <span
                                  className={`text-[12px] sm:text-[13px] font-medium truncate ${
                                    skill.enabled
                                      ? "text-[var(--theme-text)]"
                                      : "text-[var(--theme-primary)]"
                                  }`}
                                >
                                  {skill.name}
                                </span>
                              </div>
                              <p className="text-xs sm:text-xs text-[var(--theme-text-secondary)] truncate mt-0.5 leading-relaxed text-left">
                                {skill.description ||
                                  t("skillSelector.noDescription")}
                              </p>
                            </div>
                            <Checkbox
                              checked={skill.enabled}
                              pending={pendingSet.has(skill.name)}
                              disabled={personaControlled || isMutating}
                              onChange={async () => {
                                if (personaControlled || isMutating) {
                                  return;
                                }
                                const ok = await onToggleSkill(skill.name);
                                showSingleToggleToast(!skill.enabled, ok);
                              }}
                            />
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            );
          },
        )}
        {filteredSkills.length === 0 && (
          <div className="rounded-lg border border-dashed border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-4 py-6 text-center text-sm text-[var(--theme-text-secondary)]">
            {t("skills.noMatchingSkills")}
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="px-4 sm:px-5 py-3 sm:py-3.5 border-t border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] pb-[max(0.75rem,env(safe-area-inset-bottom))]">
        <button
          onClick={() => setIsOpen(false)}
          className="w-full py-2.5 px-4 bg-[var(--theme-primary)] text-white rounded-lg font-medium text-sm hover:bg-[var(--theme-primary-hover)] active:bg-[var(--theme-primary-hover)] transition-colors"
        >
          {t("skillSelector.done")}
        </button>
      </div>
    </div>
  );

  // When controlled externally, only render the modal — no trigger button
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
              className="fixed z-[301] sm:inset-0 sm:flex sm:items-center sm:justify-center sm:p-4 inset-x-0 bottom-0 animate-slide-up sm:animate-scale-in"
              onClick={() => setIsOpen(false)}
            >
              <ModalContent />
            </div>
          </>,
          document.body,
        )
      : null;
  }

  // 空状态：没有技能时显示禁用状态的图标（仅非外部控制模式）
  if (totalCount === 0) {
    return (
      <div className="relative" onClick={(e) => e.stopPropagation()}>
        <div
          className="flex items-center justify-center rounded-full p-2 border border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)] cursor-not-allowed opacity-60"
          title={t("skillSelector.noSkills")}
        >
          <Sparkles size={18} />
        </div>
      </div>
    );
  }

  return (
    <div className="relative" onClick={(e) => e.stopPropagation()}>
      {/* Trigger - ChatGPT style circular button */}
      <button
        type="button"
        onClick={(e) => {
          e.preventDefault();
          setIsOpen(true);
        }}
        className="chat-tool-btn"
        title={`${enabledCount}/${totalCount} ${t(
          "skillSelector.skillsEnabled",
        )}`}
      >
        <Sparkles size={18} />
      </button>

      {/* Modal */}
      {isOpen &&
        createPortal(
          <>
            <div
              data-yields-sidebar
              className="fixed inset-0 z-[300] bg-[var(--theme-overlay)] animate-fade-in"
              onClick={() => setIsOpen(false)}
            />

            {/* Modal Content - Desktop: centered, Mobile: bottom sheet */}
            <div
              className="fixed z-[301] sm:inset-0 sm:flex sm:items-center sm:justify-center sm:p-4 inset-x-0 bottom-0 animate-slide-up sm:animate-scale-in"
              onClick={() => setIsOpen(false)}
            >
              <ModalContent />
            </div>
          </>,
          document.body,
        )}
    </div>
  );
}
