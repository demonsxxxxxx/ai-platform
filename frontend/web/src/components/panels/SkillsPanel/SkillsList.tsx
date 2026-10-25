import { Button, Input, Pagination, PopoverRoot, PopoverTriggerElement, PopoverPortal, PopoverPositioner, PopoverPopup, Segmented } from "@lobehub/ui/base-ui";
import { useTranslation } from "react-i18next";
import {
  Package,
  FolderOpen,
  Search,
  Tag,
  ChevronDown,
  Upload,
  X,
} from "lucide-react";
import { PanelHeader } from "../../common/PanelHeader";
import { SkillsPanelSkeleton } from "../../skeletons";
import { LobeSelectField } from "../../lobe-ui/LobeSelectField";
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
  onZipClick: (skillName?: string) => void;
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
  onZipClick,
}: SkillsListProps) {
  const { t } = useTranslation();
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
    <PopoverRoot open={isFilterOpen} onOpenChange={setIsFilterOpen}>
      <PopoverTriggerElement>
        <Button type={selectedTags.length > 0 ? "primary" : "default"} htmlType="button" aria-label={t("skills.filter")}>
          <Tag size={16} />
          <span className="hidden sm:inline">{t("skills.filter")}</span>
          {selectedTags.length > 0 ? <span>({selectedTags.length})</span> : null}
          <ChevronDown size={16} />
        </Button>
      </PopoverTriggerElement>
      <PopoverPortal>
        <PopoverPositioner placement="bottomRight">
          <PopoverPopup className="skill-filter-dropdown w-72 p-3" aria-label={t("skills.tagFilter")}>
            <div className="mb-2 flex items-center justify-between gap-2">
              <p className="text-sm font-medium">{t("skills.tagFilter")}</p>
              {hasActiveFilters ? <Button type="text" size="small" htmlType="button" onClick={clearCatalogFilters}>{t("fileLibrary.clearFilters")}</Button> : null}
            </div>
            <div className="flex max-h-56 flex-wrap gap-2 overflow-y-auto">
              {availableTags.map((tag) => <Button type={selectedTags.includes(tag) ? "primary" : "default"} size="small" key={tag} aria-pressed={selectedTags.includes(tag)} htmlType="button" onClick={() => toggleTag(tag)}>{tag}</Button>)}
            </div>
          </PopoverPopup>
        </PopoverPositioner>
      </PopoverPortal>
    </PopoverRoot>
  );

  const uploadAction = canImportSkills ? (
    <Button type={adminRelease ? "primary" : "default"}
      onClick={() => onZipClick()}
      title={t("skills.zipUploadSubtitle")}
      htmlType="button"
    >
      <Upload size={16} />
      <span>{t("skills.zipUploadAction")}</span>
    </Button>
  ) : undefined;

  return (
    <>
      {embedded && (
        <>
          <section
            className="skill-management-header"
            data-skill-management-overview
          >
            <div className="min-w-0">
              <h1 className="skill-management-header__title">
                {t("skills.managementTitle")}
              </h1>
              <p className="skill-management-header__description">
                {t("skills.managementDescription")}
              </p>
            </div>
            {uploadAction ? (
              <div className="skill-management-header__actions">
                {uploadAction}
              </div>
            ) : null}
          </section>
          <div
            data-skills-catalog-toolbar
            className={`skill-panel-header skill-catalog-toolbar skill-catalog-command ${workbenchSurface.catalog.toolbar} px-0`}
          >
            <div
              className={`skill-catalog-toolbar__row skill-catalog-command__surface ${workbenchSurface.catalog.toolbarShell}`}
            >
              <div className="skill-catalog-command__primary">
                <div className="skill-catalog-command__controls">
                  <div
                    className={`skill-catalog-toolbar__search ${workbenchSurface.catalog.toolbarSearch}`}
                  >
                  <div className="relative min-w-0 flex-1">
                    <Input
                      aria-label={t("skills.searchPlaceholder")}
                      type="text"
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      prefix={<Search size={18} />}
                      placeholder={t("skills.searchPlaceholder")}
                    />
                  </div>
                  {filterMenu}
                  </div>
                  <div
                    aria-label={t("skills.views.label")}
                    className="skill-catalog-view-switcher"
                    role="group"
                  >
                    <Segmented
                      value={catalogView}
                      onChange={setCatalogView}
                      classNames={{ item: "skill-catalog-view-switcher__item" }}
                      options={catalogViews.map((view) => ({ value: view.id, label: <span>{view.label} <span aria-hidden="true">{view.count}</span></span> }))}
                    />
                  </div>
                </div>
                {hasActiveFilters ? (
                  <Button type="text"
                    className="skill-catalog-clear"
                    onClick={clearCatalogFilters}
                    htmlType="button"
                  >
                    {t("fileLibrary.clearFilters")}
                  </Button>
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
          actions={uploadAction}
        />
      )}

      {/* Error */}
      {error && (
        <div
          role="alert"
          className="mx-4 mt-4 flex items-center justify-between rounded-lg bg-[var(--theme-danger-soft)] p-3 text-sm text-[var(--theme-danger-text)] ring-1 ring-[var(--theme-danger-ring)]"
        >
          <span>{error}</span>
          <Button type="text"
            aria-label={t("common.close")}
            onClick={clearError}
            className="hover:text-[var(--theme-danger-text)]"
            htmlType="button"
            icon={<X size={18} />}
          />
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
              <Button type="default"
                htmlType="button"
                onClick={clearCatalogFilters}
                className="mt-4"
              >
                {t("fileLibrary.clearFilters")}
              </Button>
            )}
          </div>
        ) : (
          <div className="skill-catalog-layout">
            <SkillManagementTable
              canBatch={canBatchSkills}
              canDelete={canDelete}
              canEdit={canEditSkills}
              canExport={canExport && !governedUnavailable}
              canPublish={adminRelease && canImportSkills}
              canToggle={canToggleSkills}
              onDelete={onDelete}
              onEdit={onEdit}
              onExportZip={onExportZip}
              onSelectDetail={onSelectDetail}
              onSelectSkill={onSelectSkill}
              onSelectAll={onSelectAll}
              onToggle={onToggle}
              onUploadVersion={onZipClick}
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
                  current={page} hideOnSinglePage responsive showSizeChanger={false}
                  showTotal={(total, [start, end]) => `${start}-${end} / ${total}`}
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
          <div className="skill-catalog-page-size">
            <span>{t("skills.paginationPageSize")}</span>
            <LobeSelectField
              aria-label={t("skills.paginationPageSize")}
              className="w-auto"
              onChange={(value) => setPageSize(Number(value))}
              value={String(pageSize)}
              options={[10, 20, 50].map((size) => ({
                value: String(size),
                label: t("skills.paginationRows", { count: size }),
              }))}
            />
          </div>
        </div>
      )}
    </>
  );
}
