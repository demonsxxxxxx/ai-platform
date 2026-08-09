import { useEffect, useId, useRef } from "react";
import { useTranslation } from "react-i18next";
import {
  Package,
  FolderOpen,
  Check,
  Search,
  Tag,
  ChevronDown,
  Github,
  Upload,
  X,
} from "lucide-react";
import { PanelHeader } from "../../common/PanelHeader";
import { SkillsPanelSkeleton } from "../../skeletons";
import { Pagination } from "../../common/Pagination";
import { SkillManagementTable } from "./SkillManagementTable";
import { workbenchSurface } from "../../workbench/workbenchSurface";
import type { SkillResponse } from "../../../types";

interface SkillsListProps {
  embedded?: boolean;
  governedUnavailable?: boolean;
  searchQuery: string;
  setSearchQuery: (query: string) => void;
  selectedTags: string[];
  isFilterOpen: boolean;
  setIsFilterOpen: React.Dispatch<React.SetStateAction<boolean>>;
  availableTags: string[];
  filteredSkills: SkillResponse[];
  paginatedSkills: SkillResponse[];
  total: number;
  page: number;
  pageSize: number;
  setPage: (page: number) => void;
  toggleTag: (tag: string) => void;
  clearFilters: () => void;
  isLoading: boolean;
  error: string | null;
  clearError: () => void;
  canWrite: boolean;
  canEdit: boolean;
  canExport: boolean;
  canImport: boolean;
  canBatch: boolean;
  canDelete: boolean;
  adminRelease: boolean;
  selectedNames: Set<string>;
  selectedDetail: React.ReactNode;
  selectedSkillId: string | null;
  onToggle: (name: string) => void;
  onEdit: (skill: SkillResponse) => void;
  onDelete: (name: string) => void;
  onExportZip: (name: string) => void;
  onSelectSkill: (name: string) => void;
  onSelectAll: () => void;
  onSelectDetail: (skillId: string) => void;
  onGithubClick: () => void;
  onZipClick: () => void;
}

export function SkillsList({
  embedded = false,
  governedUnavailable = false,
  searchQuery,
  setSearchQuery,
  selectedTags,
  isFilterOpen,
  setIsFilterOpen,
  availableTags,
  filteredSkills,
  paginatedSkills,
  total,
  page,
  pageSize,
  setPage,
  toggleTag,
  clearFilters,
  isLoading,
  error,
  clearError,
  canWrite,
  canEdit,
  canExport,
  canImport,
  canBatch,
  canDelete,
  adminRelease,
  selectedNames,
  selectedDetail,
  selectedSkillId,
  onToggle,
  onEdit,
  onDelete,
  onExportZip,
  onSelectSkill,
  onSelectAll,
  onSelectDetail,
  onGithubClick,
  onZipClick,
}: SkillsListProps) {
  const { t } = useTranslation();
  const filterRef = useRef<HTMLDivElement>(null);
  const filterTriggerRef = useRef<HTMLButtonElement>(null);
  const filterMenuId = useId();

  // Close filter dropdown when clicking outside
  useEffect(() => {
    if (!isFilterOpen) return;
    const handleClick = (e: MouseEvent) => {
      if (filterRef.current && !filterRef.current.contains(e.target as Node)) {
        setIsFilterOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [isFilterOpen, setIsFilterOpen]);

  useEffect(() => {
    if (!isFilterOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setIsFilterOpen(false);
      filterTriggerRef.current?.focus();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [isFilterOpen, setIsFilterOpen]);

  if (isLoading) {
    return embedded ? (
      <div className="[&_.panel-header]:hidden">
        <SkillsPanelSkeleton />
      </div>
    ) : (
      <SkillsPanelSkeleton />
    );
  }

  const hasActiveFilters =
    searchQuery.trim().length > 0 || selectedTags.length > 0;
  const canToggleSkills = canWrite && !governedUnavailable;
  const canEditSkills = canEdit && !governedUnavailable;
  const canImportSkills = canImport && !governedUnavailable;
  const canBatchSkills = canBatch && !governedUnavailable;
  const canManageSkills = canBatchSkills || canImportSkills;

  const filterMenu = availableTags.length > 0 && (
    <div className="relative shrink-0" ref={filterRef}>
      <button
        aria-controls={filterMenuId}
        aria-expanded={isFilterOpen}
        aria-haspopup="true"
        type="button"
        onClick={() => setIsFilterOpen((prev) => !prev)}
        className={`btn-secondary h-10 px-3 ${
          selectedTags.length > 0
            ? "border-[var(--theme-primary)] text-[var(--theme-text)]"
            : ""
        }`}
        ref={filterTriggerRef}
      >
        <Tag size={16} />
        <span className="hidden sm:inline">{t("adminMarketplace.tags")}</span>
        {selectedTags.length > 0 && (
          <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-[var(--theme-primary-light)] px-1 text-[11px]">
            {selectedTags.length}
          </span>
        )}
        <ChevronDown
          size={16}
          className={`transition-transform ${isFilterOpen ? "rotate-180" : ""}`}
        />
      </button>
      {isFilterOpen && (
        <div
          aria-label={t("adminMarketplace.tags")}
          className="skill-filter-dropdown absolute right-0 top-[calc(100%+0.5rem)] z-20 w-72 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-3 shadow-[0_12px_28px_rgba(15,23,42,0.12)]"
          id={filterMenuId}
          role="group"
        >
          <div className="mb-2 flex items-center justify-between">
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-[var(--theme-text-secondary)]">
              {t("adminMarketplace.tags")}
            </p>
            {hasActiveFilters && (
              <button
                type="button"
                onClick={clearFilters}
                className="text-xs text-[var(--theme-text-secondary)] transition-colors hover:text-[var(--theme-primary)]"
              >
                {t("marketplace.clearFilters")}
              </button>
            )}
          </div>
          <div className="flex max-h-56 flex-wrap gap-2 overflow-y-auto">
            {availableTags.map((tag) => (
              <button
                key={tag}
                type="button"
                onClick={() => toggleTag(tag)}
                className={`skill-tag-chip ${
                  selectedTags.includes(tag) ? "skill-tag-chip--active" : ""
                }`}
              >
                {tag}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );

  const headerActions = canManageSkills ? (
    <div className="flex items-center gap-2">
      {canBatchSkills && filteredSkills.length > 0 && (
        <button
          onClick={onSelectAll}
          className="btn-secondary h-10"
        >
          <Check size={16} />
          <span className="hidden sm:inline">
            {selectedNames.size === filteredSkills.length &&
            filteredSkills.length > 0
              ? t("common.deselectAll")
              : t("common.selectAll")}
          </span>
        </button>
      )}
      {canImportSkills && (
        <>
          <button onClick={onGithubClick} className="btn-secondary h-10">
            <Github size={16} />
            <span className="hidden sm:inline">GitHub</span>
          </button>
          <button
            onClick={onZipClick}
            className={`${adminRelease ? "btn-primary" : "btn-secondary"} h-10`}
            title={
              adminRelease
                ? t("skills.adminReleaseZipSubtitle")
                : t("skills.uploadZipTitle")
            }
          >
            <Upload size={16} />
            <span>
              {t(
                adminRelease
                  ? "skills.adminReleaseZipTitle"
                  : "skills.uploadZipTitle",
              )}
            </span>
          </button>
        </>
      )}
    </div>
  ) : undefined;

  return (
    <>
      {embedded && (
        <div
          data-skills-catalog-toolbar
          className={`skill-panel-header skill-catalog-toolbar ${workbenchSurface.catalog.toolbar} px-0`}
        >
          <div
            className={`skill-catalog-toolbar__row ${workbenchSurface.catalog.toolbarShell}`}
          >
            <div
              className={`skill-catalog-toolbar__search ${workbenchSurface.catalog.toolbarSearch}`}
            >
              <div className="relative min-w-0 flex-1">
                <Search
                  size={18}
                  className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--theme-text-secondary)]"
                />
                <input
                  aria-label={t("skills.searchPlaceholder")}
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="panel-search h-10"
                  placeholder={t("skills.searchPlaceholder")}
                />
              </div>
              {filterMenu}
            </div>
            {headerActions && (
              <div
                className={`skill-catalog-toolbar__actions ${workbenchSurface.catalog.toolbarActions}`}
              >
                {headerActions}
              </div>
            )}
          </div>
        </div>
      )}
      {!embedded && (
        <PanelHeader
          title={t("skills.title")}
          subtitle={t("skills.subtitle")}
          icon={
            <Package
              size={20}
              className="text-[var(--theme-text-secondary)]"
            />
          }
          searchValue={searchQuery}
          onSearchChange={setSearchQuery}
          searchPlaceholder={t("skills.searchPlaceholder")}
          searchAccessory={filterMenu}
          actions={headerActions}
        />
      )}

      {/* Error */}
      {error && (
        <div className="mx-4 mt-4 flex items-center justify-between rounded-lg bg-[var(--theme-danger-soft)] p-3 text-sm text-[var(--theme-danger)] ring-1 ring-[var(--theme-danger-ring)]">
          <span>{error}</span>
          <button
            aria-label={t("common.close")}
            onClick={clearError}
            className="btn-icon hover:text-[var(--theme-danger)]"
            type="button"
          >
            <X size={18} />
          </button>
        </div>
      )}

      {/* Skills List */}
      <div
        className={`min-h-0 flex-1 py-3 ${embedded ? "px-0" : "px-4"}`}
        data-skills-master-detail
      >
        {filteredSkills.length === 0 ? (
          <div className={workbenchSurface.catalog.emptyState}>
            <div className={workbenchSurface.catalog.emptyIcon}>
              <FolderOpen size={28} />
            </div>
            <p className={workbenchSurface.catalog.emptyTitle}>
              {governedUnavailable
                ? t("skills.catalogUnavailable.title")
                : hasActiveFilters
                ? t("skills.noMatchingSkills")
                : t("skills.noSkills")}
            </p>
            <p className={workbenchSurface.catalog.emptyDescription}>
              {governedUnavailable
                ? t("skills.catalogUnavailable.description")
                : t("skills.subtitle")}
            </p>
            {hasActiveFilters && (
              <button
                type="button"
                onClick={clearFilters}
                className="btn-secondary mt-4"
              >
                {t("marketplace.clearFilters")}
              </button>
            )}
          </div>
        ) : (
          <div className="grid min-w-0 gap-3 xl:grid-cols-[minmax(30rem,1.1fr)_minmax(24rem,0.9fr)] xl:items-start">
            <SkillManagementTable
              canBatch={canBatchSkills}
              canDelete={canDelete}
              canEdit={canEditSkills}
              canExport={canExport && !governedUnavailable}
              canToggle={canToggleSkills}
              onDelete={onDelete}
              onEdit={onEdit}
              onExportZip={onExportZip}
              onSelectDetail={onSelectDetail}
              onSelectSkill={onSelectSkill}
              onToggle={onToggle}
              selectedNames={selectedNames}
              selectedSkillId={selectedSkillId}
              skills={paginatedSkills}
            />
            <div
              className="min-w-0 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] shadow-[0_1px_2px_rgba(18,38,63,0.04)]"
              data-selected-skill-detail-shell
            >
              {selectedDetail}
            </div>
          </div>
        )}
      </div>

      {/* Pagination */}
      {total > pageSize && (
        <div className="enterprise-divider border-t px-3 py-3 sm:px-4">
          <Pagination
            page={page}
            pageSize={pageSize}
            total={total}
            onChange={setPage}
          />
        </div>
      )}
    </>
  );
}
