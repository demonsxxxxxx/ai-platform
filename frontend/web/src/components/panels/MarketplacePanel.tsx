import { useState, useEffect, useMemo } from "react";
import {
  X,
  ShoppingBag,
  Plus,
  RotateCw,
  Search,
  Tag,
  ChevronDown,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import toast from "react-hot-toast";
import { PanelHeader } from "../common/PanelHeader";
import { MarketplacePanelSkeleton } from "../skeletons";
import { ConfirmDialog } from "../common/ConfirmDialog";
import { SkillFormSidebar } from "./SkillsPanel/SkillFormSidebar";
import { useMarketplace } from "../../hooks/useMarketplace";
import { useSkills } from "../../hooks/useSkills";
import { useAuth } from "../../hooks/useAuth";
import { Permission } from "../../types";
import type { SkillResponse, SkillCreate } from "../../types";
import { SkillCard } from "./MarketplacePanel/SkillCard";
import { SkillPreviewModal } from "./MarketplacePanel/SkillPreviewModal";
import {
  isPermissionError,
  resolveFrontendGovernanceState,
} from "../governance/frontendGovernanceState";
import { workbenchSurface } from "../workbench/workbenchSurface";

interface MarketplacePanelProps {
  embedded?: boolean;
  governedUnavailable?: boolean;
  onCatalogStateChange?: (state: {
    permissionDenied: boolean;
    projectionError: string | null;
    effectivePermissions: string[];
    effectivePermissionsKnown: boolean;
    readResolved: boolean;
  }) => void;
}

export function MarketplacePanel({
  embedded = false,
  governedUnavailable = false,
  onCatalogStateChange,
}: MarketplacePanelProps) {
  const { t } = useTranslation();
  const {
    hasAnyPermission,
    isAuthenticated,
    isLoading: authLoading,
  } = useAuth();
  const {
    skills,
    tags,
    effectivePermissions: marketplaceEffectivePermissions,
    effectivePermissionsKnown: marketplaceEffectivePermissionsKnown,
    catalogReadResolved: marketplaceCatalogReadResolved,
    isLoading,
    error,
    listError,
    selectedTags,
    searchQuery,
    setSearchQuery,
    toggleTag,
    clearFilters,
    fetchSkills,
    installSkill,
    updateSkill,
    createAndPublish,
    updateMarketplaceSkill,
    activateSkill,
    deleteSkill,
    loadMarketplaceSkillForEdit,
    clearError,
    previewSkill,
    previewFiles,
    previewLoading,
    previewFileContent,
    previewBinaryFiles,
    previewFileLoading,
    openPreview,
    readPreviewFile,
    closePreview,
    setPreviewFileContent,
  } = useMarketplace({ enabled: !governedUnavailable });
  const permissionDenied = isPermissionError(listError);
  const effectiveGovernedUnavailable = governedUnavailable || permissionDenied;
  const governanceState = resolveFrontendGovernanceState({
    isAuthenticated,
    isLoading: authLoading,
    hasWorkspace: true,
    hasPermission: !permissionDenied,
    featureEnabled: true,
    projectionError: permissionDenied ? null : listError,
  });

  const {
    skills: userSkills,
    fetchSkills: fetchUserSkills,
    isLoading: userSkillsLoading,
    getSkill,
    effectivePermissions: userEffectivePermissions,
  } = useSkills({ enabled: !effectiveGovernedUnavailable });
  const marketplaceDirectWriteBacked = true;
  const catalogEffectivePermissions = useMemo(
    () =>
      marketplaceEffectivePermissions.length > 0
        ? marketplaceEffectivePermissions
        : userEffectivePermissions,
    [marketplaceEffectivePermissions, userEffectivePermissions],
  );
  const effectivePermissions = new Set(catalogEffectivePermissions);
  const hasEffectiveSkillWrite =
    hasAnyPermission([Permission.SKILL_WRITE]) ||
    effectivePermissions.has(Permission.SKILL_WRITE);
  const hasEffectiveMarketplaceRead =
    hasAnyPermission([Permission.MARKETPLACE_READ]) ||
    effectivePermissions.has(Permission.MARKETPLACE_READ);
  const hasEffectiveMarketplaceAdmin =
    hasAnyPermission([Permission.MARKETPLACE_ADMIN]) ||
    effectivePermissions.has(Permission.MARKETPLACE_ADMIN);
  const canInstall =
    hasEffectiveSkillWrite &&
    hasEffectiveMarketplaceRead &&
    !effectiveGovernedUnavailable;
  const canCreateInMarketplace =
    marketplaceDirectWriteBacked &&
    hasEffectiveMarketplaceAdmin &&
    !effectiveGovernedUnavailable;
  const canAdmin =
    marketplaceDirectWriteBacked &&
    hasEffectiveMarketplaceAdmin;

  useEffect(() => {
    onCatalogStateChange?.({
      permissionDenied,
      projectionError: permissionDenied ? null : listError,
      effectivePermissions: catalogEffectivePermissions,
      effectivePermissionsKnown:
        marketplaceEffectivePermissionsKnown ||
        userEffectivePermissions.length > 0,
      readResolved: marketplaceCatalogReadResolved,
    });
  }, [
    catalogEffectivePermissions,
    listError,
    marketplaceCatalogReadResolved,
    marketplaceEffectivePermissionsKnown,
    onCatalogStateChange,
    permissionDenied,
    userEffectivePermissions.length,
  ]);

  const installedMarketplaceNames = new Set(
    userSkills
      .filter((skill) => skill.installed_from === "marketplace")
      .map((skill) => skill.name),
  );
  const localManualConflicts = new Set(
    userSkills
      .filter((skill) => skill.installed_from !== "marketplace")
      .map((skill) => skill.name),
  );

  useEffect(() => {
    fetchUserSkills();
  }, [fetchUserSkills]);

  // Install confirmation dialog
  const [installConfirm, setInstallConfirm] = useState<{
    isOpen: boolean;
    skillName: string;
    action: "install" | "update";
  } | null>(null);
  const [installingSkill, setInstallingSkill] = useState<string | null>(null);

  // Filter & edit state
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [isFilterOpen, setIsFilterOpen] = useState(false);
  const [editingSkill, setEditingSkill] = useState<SkillResponse | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [openMenuName, setOpenMenuName] = useState<string | null>(null);

  // Close all dropdowns when clicking outside
  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (isFilterOpen && !target.closest("[data-filter-menu]")) {
        setIsFilterOpen(false);
      }
      if (openMenuName && !target.closest("[data-mp-menu]")) {
        setOpenMenuName(null);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [isFilterOpen, openMenuName]);

  // Admin delete confirmation
  const [adminDeleteConfirm, setAdminDeleteConfirm] = useState<{
    isOpen: boolean;
    skillName: string;
  } | null>(null);

  const handleActivate = async (skillName: string, isActive: boolean) => {
    const success = await activateSkill(skillName, isActive);
    if (success) {
      toast.success(
        isActive
          ? t("marketplace.activateSuccess")
          : t("marketplace.deactivateSuccess"),
      );
    }
  };

  const handleAdminDelete = (skillName: string) => {
    setAdminDeleteConfirm({ isOpen: true, skillName });
  };

  const confirmAdminDelete = async () => {
    if (!adminDeleteConfirm) return;
    const success = await deleteSkill(adminDeleteConfirm.skillName);
    if (success) {
      toast.success(t("marketplace.deleteSuccess"));
      await fetchUserSkills();
    }
    setAdminDeleteConfirm(null);
  };

  const handleInstallClick = (skillName: string) => {
    const action = installedMarketplaceNames.has(skillName)
      ? "update"
      : "install";
    setInstallConfirm({ isOpen: true, skillName, action });
  };

  const confirmInstall = async () => {
    if (!installConfirm) return;

    const { skillName, action } = installConfirm;
    setInstallingSkill(skillName);

    try {
      const success =
        action === "install"
          ? await installSkill(skillName)
          : await updateSkill(skillName);

      if (success) {
        toast.success(
          action === "install"
            ? t("marketplace.installSuccess", { name: skillName })
            : t("marketplace.updateSuccess", { name: skillName }),
        );
        await fetchUserSkills();
      } else {
        if (action === "install" && localManualConflicts.has(skillName)) {
          toast.error(t("marketplace.installNameConflict"));
        } else {
          toast.error(
            action === "install"
              ? t("marketplace.installFailed")
              : t("marketplace.updateFailed"),
          );
        }
      }
    } finally {
      setInstallingSkill(null);
      setInstallConfirm(null);
    }
  };

  const cancelInstall = () => {
    setInstallConfirm(null);
  };

  const handleEdit = async (skillName: string) => {
    let fullSkill = await getSkill(skillName);
    if (!fullSkill) {
      fullSkill = await loadMarketplaceSkillForEdit(skillName);
      if (!fullSkill) {
        toast.error(t("marketplace.loadFailed"));
        return;
      }
    }
    setEditingSkill(fullSkill);
    setIsCreating(false);
  };

  const handleCreate = () => {
    setEditingSkill(null);
    setIsCreating(true);
    setShowCreateModal(true);
  };

  const handleSave = async (data: SkillCreate): Promise<boolean> => {
    try {
      let success = false;
      if (isCreating) {
        success = await createAndPublish({
          skill_name: data.name,
          description: data.description,
          tags: data.tags,
          version: "1.0.0",
        });
      } else if (editingSkill) {
        success = await updateMarketplaceSkill(editingSkill.name, {
          skill_name: editingSkill.name,
          description: data.description,
          tags: data.tags,
          version: "1.0.0",
        });
      }
      if (success) {
        setEditingSkill(null);
        setIsCreating(false);
        setShowCreateModal(false);
        await fetchSkills();
        await fetchUserSkills();
        toast.success(
          isCreating
            ? t("marketplace.publishSuccess", { name: data.name })
            : t("marketplace.republishSuccess", { name: editingSkill?.name }),
        );
      }
      return success;
    } catch {
      return false;
    }
  };

  const handleFormCancel = () => {
    setEditingSkill(null);
    setIsCreating(false);
    setShowCreateModal(false);
  };

  const hasActiveFilters = selectedTags.length > 0 || searchQuery.length > 0;

  const filterMenu = tags.length > 0 && (
    <div className="relative shrink-0" data-filter-menu>
      <button
        type="button"
        onClick={() => setIsFilterOpen((prev) => !prev)}
        className={`btn-secondary h-10 px-3 ${
          selectedTags.length > 0
            ? "border-[var(--theme-primary)] text-[var(--theme-text)]"
            : ""
        }`}
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
        <div className="skill-filter-dropdown absolute right-0 top-[calc(100%+0.5rem)] z-20 w-72 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-3 shadow-[0_12px_28px_rgba(15,23,42,0.12)]">
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
            {tags.map((tag) => (
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

  const headerActions = (
    <>
      {canCreateInMarketplace && (
        <button onClick={handleCreate} className="btn-primary h-10">
          <Plus size={16} />
          <span className="hidden sm:inline">
            {t("marketplace.createAndPublish")}
          </span>
        </button>
      )}
      <button
        onClick={() => fetchSkills()}
        disabled={effectiveGovernedUnavailable}
        className="btn-secondary h-10"
        title={t("common.refresh")}
      >
        <RotateCw size={16} />
      </button>
    </>
  );

  if (isLoading) {
    return embedded ? (
      <div className="[&_.panel-header]:hidden">
        <MarketplacePanelSkeleton />
      </div>
    ) : (
      <MarketplacePanelSkeleton />
    );
  }

  return (
    <div
      data-phase1c-surface="marketplace"
      data-frontend-governance-state={governanceState}
      data-marketplace-catalog-shell
      className={workbenchSurface.page}
    >
      {embedded && (
        <div
          data-marketplace-catalog-toolbar
          className={`skill-panel-header skill-catalog-toolbar ${workbenchSurface.catalog.toolbar}`}
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
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="panel-search h-10"
                  placeholder={t("marketplace.searchPlaceholder")}
                />
              </div>
              {filterMenu}
            </div>
            <div
              className={`skill-catalog-toolbar__actions ${workbenchSurface.catalog.toolbarActions}`}
            >
              {headerActions}
            </div>
          </div>
        </div>
      )}
      {!embedded && (
        <PanelHeader
          className="skill-panel-header"
          title={t("marketplace.title")}
          subtitle={t("marketplace.subtitle")}
          icon={
            <ShoppingBag
              size={20}
              className="text-[var(--theme-text-secondary)]"
            />
          }
          searchValue={searchQuery}
          onSearchChange={setSearchQuery}
          searchPlaceholder={t("marketplace.searchPlaceholder")}
          searchAccessory={filterMenu}
          actions={headerActions}
        />
      )}

      {/* Error */}
      {error && (
        <div className="mx-4 mt-4 flex items-center justify-between rounded-lg bg-[var(--theme-danger-soft)] p-3 text-sm text-[var(--theme-danger)] ring-1 ring-[var(--theme-danger-ring)]">
          <span>{error}</span>
          <button
            onClick={clearError}
            className="btn-icon hover:text-[var(--theme-danger)]"
          >
            <X size={18} />
          </button>
        </div>
      )}

      {/* Skills List */}
      <div className={workbenchSurface.catalog.content}>
        {effectiveGovernedUnavailable ? (
          <div
            data-marketplace-forbidden-shell
            className={workbenchSurface.catalog.emptyState}
          >
            <div className={workbenchSurface.catalog.emptyIcon}>
              <ShoppingBag size={28} />
            </div>
            <div>
              <p className={workbenchSurface.catalog.emptyTitle}>
                {t("marketplace.catalogUnavailable.title")}
              </p>
              <p className={workbenchSurface.catalog.emptyDescription}>
                {t("marketplace.catalogUnavailable.description")}
              </p>
            </div>
          </div>
        ) : skills.length === 0 ? (
          <div className={workbenchSurface.catalog.emptyState}>
            <div className={workbenchSurface.catalog.emptyIcon}>
              <ShoppingBag size={28} />
            </div>
            <p className={workbenchSurface.catalog.emptyTitle}>
              {searchQuery || selectedTags.length > 0
                ? t("marketplace.noMatchingSkills")
                : t("marketplace.noSkills")}
            </p>
            <p className={workbenchSurface.catalog.emptyDescription}>
              {searchQuery || selectedTags.length > 0
                ? t("marketplace.subtitle")
                : t("marketplace.createHint")}
            </p>
            {hasActiveFilters && (
              <button onClick={clearFilters} className="btn-secondary mt-4">
                {t("marketplace.clearFilters")}
              </button>
            )}
          </div>
        ) : (
          <div
            data-marketplace-catalog-grid
            className={workbenchSurface.catalog.cardGrid}
          >
            {skills.map((skill, index) => (
              <SkillCard
                key={skill.skill_name}
                skill={skill}
                index={index}
                isInstalled={installedMarketplaceNames.has(skill.skill_name)}
                hasLocalManualConflict={localManualConflicts.has(
                  skill.skill_name,
                )}
                isOwner={skill.is_owner}
                canManage={
                  marketplaceDirectWriteBacked &&
                  !effectiveGovernedUnavailable &&
                  (skill.is_owner || canAdmin)
                }
                canInstall={canInstall}
                installingSkill={installingSkill}
                userSkillsLoading={userSkillsLoading}
                selectedTags={selectedTags}
                openMenuName={openMenuName}
                onInstallClick={handleInstallClick}
                onPreview={() => openPreview(skill)}
                onToggleTag={toggleTag}
                onOpenMenu={setOpenMenuName}
                onEdit={handleEdit}
                onActivate={handleActivate}
                onDelete={handleAdminDelete}
              />
            ))}
          </div>
        )}
      </div>

      {/* Install/Update Confirmation Dialog */}
      <ConfirmDialog
        isOpen={installConfirm?.isOpen ?? false}
        title={
          installConfirm?.action === "install"
            ? t("marketplace.confirmInstall", {
                name: installConfirm?.skillName,
              })
            : t("marketplace.confirmUpdate", {
                name: installConfirm?.skillName,
              })
        }
        message={
          installConfirm?.action === "install"
            ? t("marketplace.confirmInstallMessage")
            : t("marketplace.confirmUpdateMessage")
        }
        confirmText={
          installConfirm?.action === "install"
            ? t("marketplace.install")
            : t("marketplace.update")
        }
        cancelText={t("common.cancel")}
        onConfirm={confirmInstall}
        onCancel={cancelInstall}
        variant="info"
        loading={!!installingSkill}
      />

      {/* Skill Preview Modal */}
      {previewSkill && (
        <SkillPreviewModal
          previewSkill={previewSkill}
          previewFiles={previewFiles}
          previewLoading={previewLoading}
          previewFileContent={previewFileContent}
          previewBinaryFiles={previewBinaryFiles}
          previewFileLoading={previewFileLoading}
          onClose={closePreview}
          onReadFile={readPreviewFile}
          onSetFileContent={setPreviewFileContent}
        />
      )}

      {/* Create / Edit Sidebar */}
      <SkillFormSidebar
        showModal={showCreateModal || !!editingSkill}
        isCreating={isCreating}
        editingSkill={editingSkill}
        isLoading={isLoading}
        onSave={handleSave}
        onCancel={handleFormCancel}
        createTitle={t("marketplace.createTitle")}
        subtitle={t("marketplace.createHint")}
      />

      {/* Delete Confirmation Dialog */}
      <ConfirmDialog
        isOpen={adminDeleteConfirm?.isOpen ?? false}
        title={t("marketplace.confirmDelete", {
          name: adminDeleteConfirm?.skillName,
        })}
        message={t("marketplace.confirmDeleteMessage")}
        confirmText={t("common.delete")}
        cancelText={t("common.cancel")}
        onConfirm={confirmAdminDelete}
        onCancel={() => setAdminDeleteConfirm(null)}
        variant="danger"
      />
    </div>
  );
}
