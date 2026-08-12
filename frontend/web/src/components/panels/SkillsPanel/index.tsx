import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "../../../hooks/useAuth";
import { Permission } from "../../../types";
import { isPermissionError } from "../../governance/frontendGovernanceState";
import { ConfirmDialog } from "../../common/ConfirmDialog";
import { useSkillsActions } from "./useSkillsActions";
import { SkillsList } from "./SkillsList";
import { SkillFormSidebar } from "./SkillFormSidebar";
import { ZipUploadModal } from "./ZipUploadModal";
import { GithubImportModal } from "./GithubImportModal";
import { BatchActionBar } from "./BatchActionBar";
import { workbenchSurface } from "../../workbench/workbenchSurface";
import { SkillDistributionGovernancePanel } from "../SkillDistributionGovernancePanel";
import { isAiAdminUser } from "../capabilityAdmin";
import {
  buildSkillCatalogEntries,
  filterSkillCatalogEntries,
  resolveSkillCatalogPage,
  resolveSkillCatalogSelection,
  type ArchivedSkillCatalogEntry,
} from "./skillCatalogEntries";

interface CatalogState {
  permissionDenied: boolean;
  projectionError: string | null;
  effectivePermissions: string[];
  effectivePermissionsKnown: boolean;
  readResolved: boolean;
}

interface SkillsPanelProps {
  allAuthorizedCatalog?: boolean;
  embedded?: boolean;
  governedUnavailable?: boolean;
  showDistributionEditor?: boolean;
  onCatalogStateChange?: (state: CatalogState) => void;
}

export function SkillsPanel({
  allAuthorizedCatalog = false,
  embedded = false,
  governedUnavailable = false,
  showDistributionEditor = false,
  onCatalogStateChange,
}: SkillsPanelProps) {
  const { t } = useTranslation();
  const { hasAnyPermission, user } = useAuth();
  const isAiAdmin = isAiAdminUser(user);

  const canDelete = hasAnyPermission([Permission.SKILL_DELETE]);
  const skillFileWriteBacked = true;
  const skillImportBacked = true;
  const skillBatchWriteBacked = true;
  const [selectedSkillId, setSelectedSkillId] = useState<string | null>(null);
  const [archivedSkills, setArchivedSkills] = useState<
    ArchivedSkillCatalogEntry[]
  >([]);
  const [selectionNotice, setSelectionNotice] = useState<string | null>(null);

  const actions = useSkillsActions({
    allAuthorizedCatalog,
    enabled: !governedUnavailable,
    loadAdminCatalog: showDistributionEditor,
    onSkillsArchived: setArchivedSkills,
  });
  const permissionDenied = isPermissionError(actions.listError);
  const isGovernedUnavailable = governedUnavailable || permissionDenied;
  const effectivePermissions = new Set(actions.effectivePermissions);
  const canWrite =
    !isGovernedUnavailable &&
    isAiAdmin &&
    (hasAnyPermission([Permission.SKILL_WRITE]) ||
      effectivePermissions.has(Permission.SKILL_WRITE));
  const canDeleteSkill =
    !isGovernedUnavailable &&
    isAiAdmin &&
    (canDelete || effectivePermissions.has(Permission.SKILL_DELETE));
  const canEditSkills = skillFileWriteBacked && canWrite;
  const canExportSkills = canEditSkills;
  const canImportSkills =
    skillImportBacked && (canWrite || actions.canAdminUploadSkills);
  const canBatchSkills =
    skillBatchWriteBacked && (canWrite || canDeleteSkill);

  const catalogEntries = useMemo(
    () =>
      buildSkillCatalogEntries(
        actions.skills,
        showDistributionEditor ? actions.adminCatalogItems : [],
      ),
    [actions.adminCatalogItems, actions.skills, showDistributionEditor],
  );
  const filteredCatalogEntries = useMemo(
    () =>
      filterSkillCatalogEntries(
        catalogEntries,
        actions.searchQuery,
        actions.selectedTags,
      ),
    [actions.searchQuery, actions.selectedTags, catalogEntries],
  );
  const catalogPage = useMemo(
    () =>
      resolveSkillCatalogPage({
        entries: filteredCatalogEntries,
        page: actions.page,
        pageSize: actions.pageSize,
        localPagination: allAuthorizedCatalog,
        serverTotal: actions.total,
      }),
    [
      actions.page,
      actions.pageSize,
      actions.total,
      allAuthorizedCatalog,
      filteredCatalogEntries,
    ],
  );
  const selectableNames = useMemo(
    () =>
      filteredCatalogEntries.flatMap((entry) =>
        entry.actionName ? [entry.actionName] : [],
      ),
    [filteredCatalogEntries],
  );

  useEffect(() => {
    const resolution = resolveSkillCatalogSelection(
      filteredCatalogEntries,
      selectedSkillId,
    );
    if (!resolution.changed) {
      if (
        archivedSkills.length > 0 &&
        (!selectedSkillId ||
          !archivedSkills.some((skill) => skill.id === selectedSkillId))
      ) {
        setArchivedSkills([]);
      }
      return;
    }

    const nextEntry = resolution.selectedSkillId
      ? filteredCatalogEntries.find(
          (entry) => entry.id === resolution.selectedSkillId,
        ) ?? null
      : null;
    const archivedSelection = archivedSkills.find(
      (skill) => skill.id === selectedSkillId,
    );
    if (selectedSkillId && archivedSelection) {
      setSelectionNotice(
        nextEntry
          ? t("skills.managementTable.selectionAfterDelete", {
              deleted: archivedSelection.displayName,
              name: nextEntry.displayName,
            })
          : t("skills.managementTable.selectionAfterDeleteEmpty", {
              deleted: archivedSelection.displayName,
            }),
      );
      setArchivedSkills([]);
    } else if (selectedSkillId && nextEntry) {
      setSelectionNotice(
        t("skills.managementTable.selectionChanged", {
          name: nextEntry.displayName,
        }),
      );
    } else if (!selectedSkillId && nextEntry) {
      setSelectionNotice(null);
    }
    setSelectedSkillId(resolution.selectedSkillId);
  }, [
    archivedSkills,
    filteredCatalogEntries,
    selectedSkillId,
    t,
  ]);

  const selectedCatalogEntry = useMemo(
    () =>
      selectedSkillId
        ? filteredCatalogEntries.find((entry) => entry.id === selectedSkillId) ?? null
        : null,
    [filteredCatalogEntries, selectedSkillId],
  );
  const selectedSkill = selectedCatalogEntry?.runtimeSkill ?? null;
  const selectedAdminSkill = selectedCatalogEntry?.adminSkill ?? null;

  const selectedDetailContent = showDistributionEditor ? (
    <SkillDistributionGovernancePanel
      selectedSkill={selectedAdminSkill}
      selectedSkillId={selectedAdminSkill?.skillId ?? null}
    />
  ) : selectedSkill ? (
    <section
      aria-label={t("skills.managementTable.selectedDetail")}
      className="p-4 sm:p-5"
      data-selected-skill-detail
    >
      <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--theme-text-tertiary)]">
        {t("skills.managementTable.selectedDetail")}
      </p>
      <h2 className="mt-2 text-lg font-semibold text-[var(--theme-text)]">
        {selectedSkill.name}
      </h2>
      <p className="mt-2 text-sm leading-6 text-[var(--theme-text-secondary)]">
        {selectedSkill.description || t("skills.noDescription")}
      </p>
      <dl className="mt-5 grid grid-cols-[7rem_minmax(0,1fr)] gap-x-3 gap-y-3 border-t border-[var(--theme-border)] pt-4 text-sm">
        <dt className="text-[var(--theme-text-secondary)]">
          {t("skills.managementTable.runtimeStatus")}
        </dt>
        <dd>{selectedSkill.enabled ? t("skills.managementTable.enabled") : t("skills.managementTable.disabled")}</dd>
        <dt className="text-[var(--theme-text-secondary)]">
          {t("skills.managementTable.package")}
        </dt>
        <dd className="font-mono text-xs">{selectedSkill.expected_version || "-"}</dd>
        <dt className="text-[var(--theme-text-secondary)]">
          {t("skills.available.fileTypes")}
        </dt>
        <dd>{selectedSkill.input_modes?.filter((mode) => mode !== "chat").join(" / ") || "-"}</dd>
      </dl>
    </section>
  ) : (
    <div className="flex min-h-56 items-center justify-center p-6 text-sm text-[var(--theme-text-secondary)]">
      {t("skills.managementTable.selectDetailPrompt")}
    </div>
  );

  const selectedDetail = (
    <div className="min-h-0">
      {selectionNotice ? (
        <div
          aria-live="polite"
          className="border-b border-[var(--theme-border)] bg-[var(--theme-primary-light)] px-4 py-2.5 text-xs text-[var(--theme-text-secondary)]"
          data-skill-selection-status
          role="status"
        >
          {selectionNotice}
        </div>
      ) : selectedCatalogEntry ? (
        <span
          aria-live="polite"
          className="sr-only"
          data-skill-selection-status
          role="status"
        >
          {t("skills.managementTable.selectionCurrent", {
            name: selectedCatalogEntry.displayName,
          })}
        </span>
      ) : null}
      {selectedDetailContent}
    </div>
  );

  const handleSelectDetail = (skillId: string) => {
    if (skillId === selectedSkillId) return;
    const entry = filteredCatalogEntries.find((item) => item.id === skillId);
    setSelectedSkillId(skillId);
    setSelectionNotice(
      t("skills.managementTable.selectionChanged", {
        name: entry?.displayName ?? skillId,
      }),
    );
  };

  useEffect(() => {
    onCatalogStateChange?.({
      permissionDenied,
      projectionError: permissionDenied ? null : actions.listError,
      effectivePermissions: actions.effectivePermissions,
      effectivePermissionsKnown: actions.effectivePermissionsKnown,
      readResolved: actions.catalogReadResolved,
    });
  }, [
    actions.catalogReadResolved,
    actions.effectivePermissions,
    actions.effectivePermissionsKnown,
    actions.listError,
    onCatalogStateChange,
    permissionDenied,
  ]);

  return (
    <div
      className={
        embedded
          ? "flex min-h-0 flex-col text-[var(--theme-text)]"
          : workbenchSurface.page
      }
      data-skill-workbench-shell
    >
      <SkillsList
        embedded={embedded}
        searchQuery={actions.searchQuery}
        setSearchQuery={actions.setSearchQuery}
        selectedTags={actions.selectedTags}
        isFilterOpen={actions.isFilterOpen}
        setIsFilterOpen={actions.setIsFilterOpen}
        availableTags={actions.availableTags}
        catalogEntries={filteredCatalogEntries}
        paginatedCatalogEntries={catalogPage.entries}
        total={catalogPage.total}
        page={actions.page}
        pageSize={actions.pageSize}
        setPage={actions.setPage}
        toggleTag={actions.toggleTag}
        clearFilters={actions.clearFilters}
        isLoading={actions.isLoading}
        error={actions.error}
        clearError={actions.clearError}
        canWrite={canWrite && !isGovernedUnavailable}
        canEdit={canEditSkills && !isGovernedUnavailable}
        canExport={canExportSkills && !isGovernedUnavailable}
        canImport={canImportSkills && !isGovernedUnavailable}
        canBatch={canBatchSkills && !isGovernedUnavailable}
        canDelete={canDeleteSkill && !isGovernedUnavailable}
        adminRelease={actions.canAdminUploadSkills}
        governedUnavailable={isGovernedUnavailable}
        selectedNames={actions.selectedNames}
        onToggle={actions.handleToggle}
        onEdit={actions.handleEdit}
        onDelete={actions.handleDelete}
        onExportZip={actions.handleExportZip}
        onSelectSkill={actions.handleSelectSkill}
        onSelectAll={() => actions.handleSelectAll(selectableNames)}
        onSelectDetail={handleSelectDetail}
        onGithubClick={actions.handleGithubClick}
        onZipClick={actions.handleZipClick}
        selectedDetail={selectedDetail}
        selectedSkillId={selectedSkillId}
      />

      <SkillFormSidebar
        showModal={actions.showModal}
        isCreating={false}
        editingSkill={actions.editingSkill}
        isLoading={actions.isLoading}
        onSave={actions.handleSave}
        onCancel={actions.handleCancel}
      />

      <ZipUploadModal
        showZipModal={actions.showZipModal}
        setShowZipModal={actions.setShowZipModal}
        zipFile={actions.zipFile}
        zipUploading={actions.zipUploading}
        zipPreviewing={actions.zipPreviewing}
        zipSkills={actions.zipSkills}
        selectedZipSkills={actions.selectedZipSkills}
        adminRelease={actions.canAdminUploadSkills}
        adminReleasePhase={actions.adminReleasePhase}
        adminReleaseBlocked={actions.adminReleaseBlocked}
        adminCatalogItems={actions.adminCatalogItems}
        zipInputRef={actions.zipInputRef}
        isDragging={actions.isDragging}
        onZipFileChange={actions.handleZipFileChange}
        onDragOver={actions.handleDragOver}
        onDragLeave={actions.handleDragLeave}
        onDrop={actions.handleDrop}
        onZipSkillToggle={actions.handleZipSkillToggle}
        onZipSelectAll={actions.handleZipSelectAll}
        onZipUpload={actions.handleZipUpload}
      />

      <GithubImportModal
        showGithubModal={actions.showGithubModal}
        setShowGithubModal={actions.setShowGithubModal}
        githubUrl={actions.githubUrl}
        setGithubUrl={actions.setGithubUrl}
        githubBranch={actions.githubBranch}
        setGithubBranch={actions.setGithubBranch}
        githubSkills={actions.githubSkills}
        selectedGithubSkills={actions.selectedGithubSkills}
        githubLoading={actions.githubLoading}
        githubInstalling={actions.githubInstalling}
        githubExporting={actions.githubExporting}
        onGithubPreview={actions.handleGithubPreview}
        onGithubSkillToggle={actions.handleGithubSkillToggle}
        onGithubInstall={actions.handleGithubInstall}
        onGithubExport={actions.handleGithubExport}
        setSelectedGithubSkills={actions.setSelectedGithubSkills}
      />

      {actions.selectionMode && canBatchSkills && (
        <BatchActionBar
          selectedCount={actions.selectedNames.size}
          batchLoading={actions.batchLoading}
          canWrite={canBatchSkills && canWrite && !isGovernedUnavailable}
          canDelete={canBatchSkills && canDeleteSkill && !isGovernedUnavailable}
          onBatchToggle={actions.handleBatchToggle}
          onBatchDelete={actions.handleBatchDelete}
          onClearSelection={actions.clearSelection}
        />
      )}

      <ConfirmDialog
        isOpen={actions.isDeleteConfirmOpen}
        title={t("skills.confirmDelete", {
          name: actions.deleteConfirmData?.name || "",
        })}
        message={t("skills.confirmDeleteMessage", {
          name: actions.deleteConfirmData?.name || "",
        })}
        confirmText={t("skills.archiveAction")}
        cancelText={t("common.cancel")}
        onConfirm={actions.confirmDelete}
        onCancel={actions.cancelDelete}
        variant="warning"
        loading={actions.isDeleting}
      />
    </div>
  );
}
