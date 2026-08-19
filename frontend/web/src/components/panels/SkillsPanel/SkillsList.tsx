import { useEffect, useId, useRef } from "react";
import { useTranslation } from "react-i18next";
import {
  Package,
  Archive,
  Boxes,
  CheckCircle2,
  Eye,
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
import {
  resolveSkillCatalogMetrics,
  type SkillCatalogEntry,
  type SkillCatalogView,
} from "./skillCatalogEntries";

interface SkillsListProps {
  embedded?: boolean;
  governedUnavailable?: boolean;
  searchQuery: string;
  setSearchQuery: (query: string) => void;
  selectedTags: string[];
  isFilterOpen: boolean;
  setIsFilterOpen: React.Dispatch<React.SetStateAction<boolean>>;
  availableTags: string[];
  catalogEntries: SkillCatalogEntry[];
  metricsCatalogEntries: SkillCatalogEntry[];
  paginatedCatalogEntries: SkillCatalogEntry[];
  total: number;
  page: number;
  pageSize: number;
  setPage: (page: number) => void;
  setPageSize: (pageSize: number) => void;
  catalogView: SkillCatalogView;
  setCatalogView: (view: SkillCatalogView) => void;
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
  onEdit: (skill: NonNullable<SkillCatalogEntry["runtimeSkill"]>) => void;
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
  catalogEntries,
  metricsCatalogEntries,
  paginatedCatalogEntries,
  total,
  page,
  pageSize,
  setPage,
  setPageSize,
  catalogView,
  setCatalogView,
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
    searchQuery.trim().length > 0 ||
    selectedTags.length > 0 ||
    catalogView !== "all";
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const canToggleSkills = canWrite && !governedUnavailable;
  const canEditSkills = canEdit && !governedUnavailable;
  const canImportSkills = canImport && !governedUnavailable;
  const canBatchSkills = canBatch && !governedUnavailable;
  const canManageSkills = canBatchSkills || canImportSkills;
  const selectableNames = catalogEntries.flatMap((entry) =>
    entry.actionName ? [entry.actionName] : [],
  );
  const allSelectableSelected =
    selectableNames.length > 0 &&
    selectableNames.every((name) => selectedNames.has(name));
  const catalogMetrics = resolveSkillCatalogMetrics(metricsCatalogEntries);
  const restrictedCount =
    catalogMetrics.total - catalogMetrics.visible - catalogMetrics.internal;
  const catalogViews: Array<{
    id: SkillCatalogView;
    label: string;
    count: number;
  }> = [
    { id: "all", label: t("skills.views.all"), count: catalogMetrics.total },
    {
      id: "available",
      label: t("skills.views.available"),
      count: catalogMetrics.visible,
    },
    {
      id: "internal",
      label: t("skills.views.internal"),
      count: catalogMetrics.internal,
    },
    {
      id: "restricted",
      label: t("skills.views.restricted"),
      count: restrictedCount,
    },
  ];
  const clearCatalogFilters = () => {
    clearFilters();
    setCatalogView("all");
  };

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
                onClick={clearCatalogFilters}
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
                aria-pressed={selectedTags.includes(tag)}
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
      {canBatchSkills && selectableNames.length > 0 && (
        <button
          aria-label={
            allSelectableSelected
              ? t("common.deselectAll")
              : t("common.selectAll")
          }
          onClick={onSelectAll}
          className="btn-secondary h-10"
          type="button"
        >
          <Check size={16} />
          <span className="hidden sm:inline">
            {allSelectableSelected
              ? t("common.deselectAll")
              : t("common.selectAll")}
          </span>
        </button>
      )}
      {canImportSkills && (
        <>
          <button
            aria-label={t("skills.importFromGitHub")}
            onClick={onGithubClick}
            className="btn-secondary h-10"
            type="button"
          >
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
            type="button"
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
        <>
          <section
            className="skill-management-hero"
            data-skill-management-overview
          >
            <div className="skill-management-hero__intro">
              <div className="skill-management-hero__title-row">
                <span className="skill-management-hero__mark">
                  <Package aria-hidden="true" size={20} />
                </span>
                <div className="min-w-0">
                  <p className="skill-management-hero__eyebrow">
                    {t("skills.managementEyebrow")}
                  </p>
                  <h1 className="skill-management-hero__title">
                    {t("skills.managementTitle")}
                  </h1>
                </div>
              </div>
              <p className="skill-management-hero__description">
                {t("skills.managementDescription")}
              </p>
              <p className="skill-management-hero__policy">
                <Archive aria-hidden="true" size={14} />
                <span>{t("skills.archivePolicyDescription")}</span>
              </p>
            </div>
            <dl
              className="skill-management-metrics"
              data-skill-management-metrics
            >
              <div className="skill-management-metric">
                <dt>
                  <Package aria-hidden="true" size={14} />
                  {t("skills.metrics.total")}
                </dt>
                <dd>{catalogMetrics.total}</dd>
              </div>
              <div className="skill-management-metric skill-management-metric--enabled">
                <dt>
                  <CheckCircle2 aria-hidden="true" size={14} />
                  {t("skills.metrics.enabled")}
                </dt>
                <dd>{catalogMetrics.enabled}</dd>
              </div>
              <div className="skill-management-metric skill-management-metric--visible">
                <dt>
                  <Eye aria-hidden="true" size={14} />
                  {t("skills.metrics.visible")}
                </dt>
                <dd>{catalogMetrics.visible}</dd>
              </div>
              <div className="skill-management-metric skill-management-metric--internal">
                <dt>
                  <Boxes aria-hidden="true" size={14} />
                  {t("skills.metrics.internal")}
                </dt>
                <dd>{catalogMetrics.internal}</dd>
              </div>
            </dl>
          </section>
          <div
            data-skills-catalog-toolbar
            className={`skill-panel-header skill-catalog-toolbar skill-catalog-command ${workbenchSurface.catalog.toolbar} px-0`}
          >
            <div
              className={`skill-catalog-toolbar__row skill-catalog-command__surface ${workbenchSurface.catalog.toolbarShell}`}
            >
              <div className="skill-catalog-command__primary">
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
              <div className="skill-catalog-command__secondary">
                <div
                  aria-label={t("skills.views.label")}
                  className="skill-catalog-view-switcher"
                  role="group"
                >
                  {catalogViews.map((view) => (
                    <button
                      aria-pressed={catalogView === view.id}
                      className="skill-catalog-view-switcher__item"
                      key={view.id}
                      onClick={() => setCatalogView(view.id)}
                      type="button"
                    >
                      <span>{view.label}</span>
                      <span aria-hidden="true">{view.count}</span>
                    </button>
                  ))}
                </div>
                {hasActiveFilters ? (
                  <button
                    className="skill-catalog-clear"
                    onClick={clearCatalogFilters}
                    type="button"
                  >
                    {t("marketplace.clearFilters")}
                  </button>
                ) : null}
              </div>
            </div>
          </div>
        </>
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
        {catalogEntries.length === 0 ? (
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
                onClick={clearCatalogFilters}
                className="btn-secondary mt-4"
              >
                {t("marketplace.clearFilters")}
              </button>
            )}
          </div>
        ) : (
          <div className="skill-catalog-layout">
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
              entries={paginatedCatalogEntries}
            />
            <div
              className="skill-catalog-detail-shell"
              data-selected-skill-detail-shell
            >
              {selectedDetail}
            </div>
          </div>
        )}
      </div>

      {/* Pagination */}
      {total > 0 && (
        <div className="skill-catalog-pagination enterprise-divider">
          <div className="skill-catalog-pagination__navigation">
            {total > pageSize ? (
              <Pagination
                page={page}
                pageSize={pageSize}
                total={total}
                onChange={setPage}
              />
            ) : (
              <p className="text-xs text-[var(--theme-text-secondary)] sm:text-sm">
                {t("skills.paginationSummary", {
                  total,
                  page,
                  pages: totalPages,
                })}
              </p>
            )}
          </div>
          <label className="skill-catalog-page-size">
            <span>{t("skills.paginationPageSize")}</span>
            <select
              aria-label={t("skills.paginationPageSize")}
              onChange={(event) => setPageSize(Number(event.target.value))}
              value={pageSize}
            >
              {[10, 20, 50].map((size) => (
                <option key={size} value={size}>
                  {t("skills.paginationRows", { count: size })}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}
    </>
  );
}
