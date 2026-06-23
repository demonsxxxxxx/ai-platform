import { useState, useEffect } from "react";
import {
  X,
  ShoppingBag,
  Plus,
  RotateCw,
  Search,
  Tag,
  ChevronDown,
  Building2,
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
import { GroupAvailabilityToggleRow } from "../governance/GroupAvailabilityToggleRow";
import { resolveFrontendGovernanceState } from "../governance/frontendGovernanceState";

interface MarketplacePanelProps {
  embedded?: boolean;
  governedUnavailable?: boolean;
  settingsStateDegraded?: boolean;
}

export function MarketplacePanel({
  embedded = false,
  governedUnavailable = false,
  settingsStateDegraded = false,
}: MarketplacePanelProps) {
  const { t } = useTranslation();
  const {
    hasAnyPermission,
    isAuthenticated,
    isLoading: authLoading,
  } = useAuth();
  const canReadMarketplace = hasAnyPermission([Permission.MARKETPLACE_READ]);
  const effectiveGovernedUnavailable =
    governedUnavailable || !canReadMarketplace;
  const governanceState = resolveFrontendGovernanceState({
    isAuthenticated,
    isLoading: authLoading,
    hasWorkspace: true,
    hasPermission: canReadMarketplace,
    featureEnabled: true,
    degraded: settingsStateDegraded,
  });
  const {
    skills,
    tags,
    isLoading,
    error,
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
  } = useMarketplace({ enabled: !effectiveGovernedUnavailable });

  const {
    skills: userSkills,
    fetchSkills: fetchUserSkills,
    isLoading: userSkillsLoading,
    getSkill,
  } = useSkills({ enabled: !effectiveGovernedUnavailable });
  const marketplaceDirectWriteBacked = true;
  const canInstall =
    hasAnyPermission([Permission.SKILL_WRITE]) &&
    hasAnyPermission([Permission.MARKETPLACE_READ]) &&
    !effectiveGovernedUnavailable;
  const canCreateInMarketplace =
    marketplaceDirectWriteBacked &&
    hasAnyPermission([Permission.MARKETPLACE_ADMIN]) &&
    !effectiveGovernedUnavailable;
  const canAdmin =
    marketplaceDirectWriteBacked &&
    hasAnyPermission([Permission.MARKETPLACE_ADMIN]);

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
  const marketplacePlaceholderItems = [
    {
      id: "department-skills",
      title: t("marketplace.emptyDepartmentCatalog.title", "Department Skills"),
      description: t(
        "marketplace.emptyDepartmentCatalog.description",
        "Skills approved for your department will appear here after an administrator enables them.",
      ),
    },
    {
      id: "approved-tools",
      title: t("marketplace.approvedCatalog.title", "Approved toolkits"),
      description: t(
        "marketplace.approvedCatalog.description",
        "Company-approved Skills and MCP-backed workflows stay listed here when they are ready for your role.",
      ),
    },
    {
      id: "request-flow",
      title: t("marketplace.requestAccess.title", "Request access"),
      description: t(
        "marketplace.requestAccess.description",
        "Ask your department administrator to enable a Skill for your team before installation is available.",
      ),
    },
  ];

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
        <div className="skill-filter-dropdown absolute right-0 top-[calc(100%+0.5rem)] z-20 w-72 rounded-lg border p-3 shadow-lg">
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
      data-settings-state-degraded={settingsStateDegraded || undefined}
      className="flex h-full min-h-0 flex-col bg-[var(--theme-bg)] text-slate-950 dark:bg-stone-950 dark:text-stone-100"
    >
      {embedded && (
        <div className="skill-panel-header">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex min-w-0 flex-1 items-center gap-2">
              <div className="relative min-w-0 flex-1">
                <Search
                  size={18}
                  className="absolute left-3 top-1/2 -translate-y-1/2 text-stone-400 dark:text-stone-500"
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
            <div className="flex flex-wrap items-center gap-2">
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
              className="text-stone-600 dark:text-stone-400"
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
        <div className="mx-4 mt-4 flex items-center justify-between rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-900/30 dark:text-red-400">
          <span>{error}</span>
          <button
            onClick={clearError}
            className="btn-icon hover:text-red-900 dark:hover:text-red-300"
          >
            <X size={18} />
          </button>
        </div>
      )}

      {!embedded && (
      <div className="px-4 pt-3">
        <GroupAvailabilityToggleRow
          label={t("skills.marketplace.departmentAvailability")}
          description={t("skills.marketplace.groupToggleUnavailable")}
          state="unavailable"
          backed={false}
        />
      </div>
      )}

      {!embedded && (
        <div
          data-marketplace-filter-shell
          className="mx-4 mt-3 flex flex-col gap-2 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] p-3 text-xs text-slate-600 shadow-[0_4px_12px_rgba(18,38,63,0.03)] dark:border-stone-800 dark:bg-stone-900 dark:text-stone-300 sm:flex-row sm:items-center sm:justify-between"
        >
          <div className="flex min-w-0 items-center gap-2">
            <Building2 size={16} className="shrink-0 text-slate-500" />
            <span className="font-medium text-slate-900 dark:text-stone-100">
              {t("skills.marketplace.departmentAvailability")}
            </span>
            <span className="truncate text-slate-500 dark:text-stone-400">
              {effectiveGovernedUnavailable
                ? t("marketplace.catalogUnavailable.description")
                : t("marketplace.subtitle")}
            </span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {[
              t("marketplace.filter.availableToMe", "Available to me"),
              t("marketplace.filter.department", "Department"),
              t("marketplace.filter.approved", "Approved"),
            ].map((label) => (
              <span
                key={label}
                className="rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg)] px-2 py-1 font-medium text-slate-500 dark:border-stone-800 dark:bg-stone-950 dark:text-stone-400"
              >
                {label}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Skills List */}
      <div className="skill-content-area flex-1 overflow-y-auto py-2 sm:py-4 px-4 sm:p-6">
        {effectiveGovernedUnavailable ? (
          <div data-marketplace-unavailable-shell className="space-y-4">
            <div
              data-marketplace-ordinary-user-copy
              className="rounded-lg border border-dashed border-slate-300 bg-[var(--theme-bg-card)] p-4 text-sm leading-6 text-slate-600 dark:border-stone-700 dark:bg-stone-900 dark:text-stone-300"
            >
              <p className="font-semibold text-slate-900 dark:text-stone-100">
                {t("marketplace.catalogUnavailable.title")}
              </p>
              <p className="mt-1">
                {t("marketplace.catalogUnavailable.description")}
              </p>
            </div>
            <div
              data-marketplace-placeholder-list
              className="grid gap-3 md:grid-cols-3"
            >
              {marketplacePlaceholderItems.map((item) => (
                <article
                  key={item.id}
                  className="rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] p-4 shadow-[0_4px_12px_rgba(18,38,63,0.03)] dark:border-stone-800 dark:bg-stone-900"
                >
                  <div className="flex items-center gap-2">
                    <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-100 text-slate-500 dark:bg-stone-800 dark:text-stone-300">
                      <ShoppingBag size={16} />
                    </span>
                    <h3 className="text-sm font-semibold text-slate-900 dark:text-stone-100">
                      {item.title}
                    </h3>
                  </div>
                  <p className="mt-3 text-xs leading-5 text-slate-500 dark:text-stone-400">
                    {item.description}
                  </p>
                  <button
                    type="button"
                    disabled
                    aria-disabled
                    className="mt-4 rounded-md border border-slate-200 px-2.5 py-1.5 text-xs font-medium text-slate-400 dark:border-stone-800 dark:text-stone-500"
                  >
                    {t("composerChip.status.unavailable", "unavailable")}
                  </button>
                </article>
              ))}
            </div>
          </div>
        ) : skills.length === 0 ? (
          <div className="skill-empty-state">
            <div className="skill-empty-state__icon">
              <ShoppingBag size={28} />
            </div>
            <p className="skill-empty-state__title">
              {effectiveGovernedUnavailable
                ? t("marketplace.catalogUnavailable.title")
                : searchQuery || selectedTags.length > 0
                ? t("marketplace.noMatchingSkills")
                : t("marketplace.noSkills")}
            </p>
            <p className="skill-empty-state__description">
              {effectiveGovernedUnavailable
                ? t("marketplace.catalogUnavailable.description")
                : searchQuery || selectedTags.length > 0
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
          <div className="grid auto-grid-cols gap-5">
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
