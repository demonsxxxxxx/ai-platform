import { useTranslation } from "react-i18next";
import { useAuth } from "../../../hooks/useAuth";
import { Permission } from "../../../types";
import { ConfirmDialog } from "../../common/ConfirmDialog";
import { useSkillsActions } from "./useSkillsActions";
import { SkillsList } from "./SkillsList";
import { SkillFormSidebar } from "./SkillFormSidebar";
import { ZipUploadModal } from "./ZipUploadModal";
import { GithubImportModal } from "./GithubImportModal";
import { BatchActionBar } from "./BatchActionBar";
import { PublishDialog } from "./PublishDialog";

interface SkillsPanelProps {
  embedded?: boolean;
  governedUnavailable?: boolean;
  settingsStateDegraded?: boolean;
}

export function SkillsPanel({
  embedded = false,
  governedUnavailable = false,
  settingsStateDegraded = false,
}: SkillsPanelProps) {
  const { t } = useTranslation();
  const { hasAnyPermission } = useAuth();

  const canRead = hasAnyPermission([Permission.SKILL_READ]);
  const canDelete = hasAnyPermission([Permission.SKILL_DELETE]);
  const canPublishByAuth = hasAnyPermission([Permission.MARKETPLACE_PUBLISH]);
  const isGovernedUnavailable = governedUnavailable || !canRead;
  const skillFileWriteBacked = true;
  const skillImportBacked = true;
  const skillBatchWriteBacked = true;

  const actions = useSkillsActions({ enabled: !isGovernedUnavailable });
  const effectivePermissions = new Set(actions.effectivePermissions);
  const canWrite =
    !isGovernedUnavailable &&
    (hasAnyPermission([Permission.SKILL_WRITE]) ||
      effectivePermissions.has(Permission.SKILL_WRITE));
  const canDeleteSkill =
    !isGovernedUnavailable &&
    (canDelete || effectivePermissions.has(Permission.SKILL_DELETE));
  const canPublish =
    !isGovernedUnavailable &&
    (canPublishByAuth || effectivePermissions.has(Permission.MARKETPLACE_PUBLISH));
  const canEditSkills = skillFileWriteBacked && canWrite;
  const canCreateSkills = false;
  const canImportSkills = skillImportBacked && canWrite;
  const canBatchSkills =
    skillBatchWriteBacked && (canWrite || canDeleteSkill);

  return (
    <div
      className="flex h-full min-h-0 flex-col bg-[var(--theme-bg)] text-slate-950 dark:bg-stone-950 dark:text-stone-100"
      data-skill-workbench-shell
      data-settings-state-degraded={settingsStateDegraded || undefined}
    >
      <SkillsList
        embedded={embedded}
        searchQuery={actions.searchQuery}
        setSearchQuery={actions.setSearchQuery}
        selectedTags={actions.selectedTags}
        isFilterOpen={actions.isFilterOpen}
        setIsFilterOpen={actions.setIsFilterOpen}
        availableTags={actions.availableTags}
        filteredSkills={actions.filteredSkills}
        paginatedSkills={actions.paginatedSkills}
        total={actions.total}
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
        canCreate={canCreateSkills && !isGovernedUnavailable}
        canImport={canImportSkills && !isGovernedUnavailable}
        canBatch={canBatchSkills && !isGovernedUnavailable}
        canDelete={canDeleteSkill && !isGovernedUnavailable}
        canPublish={canPublish && !isGovernedUnavailable}
        governedUnavailable={isGovernedUnavailable}
        selectedNames={actions.selectedNames}
        onToggle={actions.handleToggle}
        onEdit={actions.handleEdit}
        onDelete={actions.handleDelete}
        onExportZip={actions.handleExportZip}
        onPublish={
          canPublish && !isGovernedUnavailable
            ? (s) => {
                actions.setPublishConfirm({
                  isOpen: true,
                  localSkillName: s.name,
                  marketplaceSkillName: s.published_marketplace_name || s.name,
                  description: s.description || "",
                  tagsInput: s.tags?.join(", ") || "",
                  isPublished: s.is_published,
                });
              }
            : undefined
        }
        onSelectSkill={actions.handleSelectSkill}
        onSelectAll={actions.handleSelectAll}
        onCreate={actions.handleCreate}
        onGithubClick={actions.handleGithubClick}
        onZipClick={actions.handleZipClick}
      />

      <SkillFormSidebar
        showModal={actions.showModal}
        isCreating={actions.isCreating}
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
        confirmText={t("common.delete")}
        cancelText={t("common.cancel")}
        onConfirm={actions.confirmDelete}
        onCancel={actions.cancelDelete}
        variant="danger"
      />

      <PublishDialog
        publishConfirm={actions.publishConfirm}
        setPublishConfirm={actions.setPublishConfirm}
        onConfirm={actions.confirmPublish}
      />
    </div>
  );
}
