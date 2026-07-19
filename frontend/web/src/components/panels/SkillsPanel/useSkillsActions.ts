import { useMemo, useState, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import toast from "react-hot-toast";
import { useLocation, useNavigate } from "react-router-dom";
import { exportProjectZip } from "../../../utils/exportProjectZip";
import { useAuth } from "../../../hooks/useAuth";
import { useSkills } from "../../../hooks/useSkills";
import type { AdminSkillCatalogItem } from "../../../services/api/skill";
import { sanitizeSkillName } from "../../../utils/skillFilters";
import { type SkillResponse, type SkillCreate } from "../../../types";
import { isAiAdminUser } from "../capabilityAdmin";
import {
  adminReleaseActionForStatus,
  coerceZipSkillSelection,
  initialZipSkillSelection,
  toggleZipSkillSelection,
  type ZipSkillPreview,
} from "./zipSelection";

export type { ZipSkillPreview } from "./zipSelection";

export type AdminSkillReleasePhase =
  | "idle"
  | "uploading"
  | "reviewing"
  | "promoting"
  | "refreshing";

interface GitHubSkill {
  name: string;
  path: string;
  description: string;
}

export function useSkillsActions(options?: { enabled?: boolean }) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const enabled = options?.enabled !== false;
  // Search & filter
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [isFilterOpen, setIsFilterOpen] = useState(false);

  // Pagination
  const [page, setPage] = useState(1);
  const pageSize = 20;
  const listParams = useMemo(
    () => ({
      skip: (page - 1) * pageSize,
      limit: pageSize,
      q: searchQuery.trim() || undefined,
      tags: selectedTags.length > 0 ? selectedTags : undefined,
    }),
    [page, pageSize, searchQuery, selectedTags],
  );

  const {
    skills,
    availableTags,
    effectivePermissions,
    effectivePermissionsKnown,
    catalogReadResolved,
    total,
    isLoading,
    error,
    listError,
    getSkill,
    getFullSkill,
    createSkill,
    updateSkill,
    deleteSkill,
    batchDeleteSkills,
    batchToggleSkills,
    toggleSkill,
    uploadSkill,
    adminUploadSkill,
    adminReviewSkillVersion,
    adminPromoteSkillVersion,
    adminListSkills,
    previewZipSkills,
    adminPreviewZipSkills,
    previewGitHubSkills,
    installGitHubSkills,
    publishToMarketplace,
    clearError,
    fetchSkills,
  } = useSkills({ enabled, listParams });
  const filteredSkills = skills;
  const canAdminUploadSkills = isAiAdminUser(user);

  useEffect(() => {
    setPage(1);
  }, [searchQuery, selectedTags]);

  useEffect(() => {
    const prefillSearch = (
      location.state as { prefillSkillSearch?: string } | null
    )?.prefillSkillSearch;
    if (!prefillSearch) {
      return;
    }
    setSearchQuery(prefillSearch);
    navigate(location.pathname, { replace: true });
  }, [location.pathname, location.state, navigate]);

  const paginatedSkills = filteredSkills;

  const toggleTag = (tag: string) => {
    setSelectedTags((prev) =>
      prev.includes(tag) ? prev.filter((item) => item !== tag) : [...prev, tag],
    );
  };

  const clearFilters = () => {
    setSearchQuery("");
    setSelectedTags([]);
  };

  // Form modal state
  const [editingSkill, setEditingSkill] = useState<SkillResponse | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [showModal, setShowModal] = useState(false);

  // Batch selection state
  const [selectedNames, setSelectedNames] = useState<Set<string>>(new Set());
  const [batchLoading, setBatchLoading] = useState(false);

  // Delete confirmation
  const [isDeleteConfirmOpen, setIsDeleteConfirmOpen] = useState(false);
  const [deleteConfirmData, setDeleteConfirmData] = useState<{
    name: string;
  } | null>(null);

  // Publish confirmation
  const [publishConfirm, setPublishConfirm] = useState<{
    isOpen: boolean;
    localSkillName: string;
    marketplaceSkillName: string;
    description: string;
    tagsInput: string;
    isPublished: boolean;
    error?: string;
  } | null>(null);

  // ZIP upload state
  const [showZipModal, setShowZipModal] = useState(false);
  const [zipFile, setZipFile] = useState<File | null>(null);
  const [zipUploading, setZipUploading] = useState(false);
  const [zipPreviewing, setZipPreviewing] = useState(false);
  const [zipSkills, setZipSkills] = useState<ZipSkillPreview[]>([]);
  const [selectedZipSkills, setSelectedZipSkills] = useState<string[]>([]);
  const [adminReleasePhase, setAdminReleasePhase] =
    useState<AdminSkillReleasePhase>("idle");
  const [adminReleaseBlocked, setAdminReleaseBlocked] = useState(false);
  const [adminCatalogItems, setAdminCatalogItems] = useState<
    AdminSkillCatalogItem[]
  >([]);
  const zipInputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);

  const refreshAdminSkillCatalog = async (): Promise<
    AdminSkillCatalogItem[] | null
  > => {
    const items = await adminListSkills();
    if (items) setAdminCatalogItems(items);
    return items;
  };

  // GitHub import state
  const [showGithubModal, setShowGithubModal] = useState(false);
  const [githubUrl, setGithubUrl] = useState("");
  const [githubBranch, setGithubBranch] = useState("main");
  const [githubSkills, setGithubSkills] = useState<GitHubSkill[]>([]);
  const [selectedGithubSkills, setSelectedGithubSkills] = useState<string[]>(
    [],
  );
  const [githubLoading, setGithubLoading] = useState(false);
  const [githubInstalling, setGithubInstalling] = useState(false);
  const [githubExporting, setGithubExporting] = useState(false);

  // CRUD handlers
  const handleCreate = () => {
    setIsCreating(true);
    setEditingSkill(null);
    setShowModal(true);
  };

  const handleEdit = async (skill: SkillResponse) => {
    const fullSkill = await getSkill(skill.name);
    setEditingSkill(fullSkill || skill);
    setIsCreating(false);
    setShowModal(true);
  };

  const handleSave = async (data: SkillCreate): Promise<boolean> => {
    let success = false;
    try {
      if (isCreating) {
        success = await createSkill(data);
      } else if (editingSkill) {
        // Use filePaths (lazy-load mode) when available, fallback to files keys
        const oldFiles = editingSkill.filePaths?.length
          ? editingSkill.filePaths
          : Object.keys(editingSkill.files);
        const newFiles = data.files ? Object.keys(data.files) : [];
        const deletedFiles = oldFiles.filter((f) => !newFiles.includes(f));
        success = await updateSkill(editingSkill.name, {
          description: data.description,
          content: data.content,
          files: data.files,
          deletedFiles,
        });
      }
      if (success) {
        setShowModal(false);
        setEditingSkill(null);
        setIsCreating(false);
      }
    } catch {
      success = false;
    }
    return success;
  };

  const handleCancel = () => {
    setShowModal(false);
    setEditingSkill(null);
    setIsCreating(false);
  };

  const handleExportZip = async (name: string) => {
    const fullSkill = await getFullSkill(name);
    if (!fullSkill) {
      toast.error(t("skills.exportFailed"));
      return;
    }
    try {
      await exportProjectZip(fullSkill.files, name);
      toast.success(t("skills.exportSuccess"));
    } catch {
      toast.error(t("skills.exportFailed"));
    }
  };

  const handleDelete = (name: string) => {
    setDeleteConfirmData({ name });
    setIsDeleteConfirmOpen(true);
  };

  const confirmDelete = async () => {
    if (!deleteConfirmData) return;
    try {
      await deleteSkill(deleteConfirmData.name);
    } finally {
      setIsDeleteConfirmOpen(false);
      setDeleteConfirmData(null);
    }
  };

  const cancelDelete = () => {
    setIsDeleteConfirmOpen(false);
    setDeleteConfirmData(null);
  };

  const handleToggle = async (name: string) => {
    await toggleSkill(name);
  };

  // Batch handlers
  const selectionMode = selectedNames.size > 0;

  const handleSelectSkill = (name: string) => {
    setSelectedNames((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const handleSelectAll = () => {
    if (selectedNames.size === filteredSkills.length) {
      setSelectedNames(new Set());
    } else {
      setSelectedNames(new Set(filteredSkills.map((s) => s.name)));
    }
  };

  const clearSelection = () => setSelectedNames(new Set());

  const handleBatchDelete = async () => {
    if (selectedNames.size === 0) return;
    setBatchLoading(true);
    try {
      await batchDeleteSkills(Array.from(selectedNames));
      clearSelection();
      toast.success(
        t("skills.batchDeleteSuccess", { count: selectedNames.size }),
      );
    } catch {
      toast.error(t("skills.batchDeleteFailed"));
    } finally {
      setBatchLoading(false);
    }
  };

  const handleBatchToggle = async (enabled: boolean) => {
    if (selectedNames.size === 0) return;
    setBatchLoading(true);
    try {
      await batchToggleSkills(Array.from(selectedNames), enabled);
      clearSelection();
      toast.success(
        enabled
          ? t("skills.batchEnableSuccess", { count: selectedNames.size })
          : t("skills.batchDisableSuccess", { count: selectedNames.size }),
      );
    } catch {
      toast.error(t("skills.batchToggleFailed"));
    } finally {
      setBatchLoading(false);
    }
  };

  // Publish handler
  const confirmPublish = async () => {
    if (!publishConfirm) return;
    const { localSkillName, marketplaceSkillName, description } =
      publishConfirm;

    if (!marketplaceSkillName.trim()) {
      setPublishConfirm({
        ...publishConfirm,
        error: t("skills.form.validation.nameRequired"),
      });
      return;
    }
    if (!description.trim()) {
      setPublishConfirm({
        ...publishConfirm,
        error: t("skills.form.validation.descriptionRequired"),
      });
      return;
    }

    const normalizedTags = Array.from(
      new Set(
        publishConfirm.tagsInput
          .split(",")
          .map((tag) => tag.trim())
          .filter(Boolean),
      ),
    );

    const success = await publishToMarketplace(localSkillName, {
      skill_name: sanitizeSkillName(marketplaceSkillName.trim()),
      description: description.trim() || undefined,
      tags: normalizedTags,
    });

    if (success) {
      toast.success(
        publishConfirm.isPublished
          ? t("skills.republishSuccess")
          : t("skills.publishSuccess"),
      );
      setPublishConfirm(null);
      return;
    }

    setPublishConfirm({
      ...publishConfirm,
      error: t("skills.publishFailed") || "Publish failed",
    });
  };

  // ZIP upload handlers
  const handleZipClick = () => {
    setZipFile(null);
    setZipSkills([]);
    setSelectedZipSkills([]);
    setAdminReleasePhase("idle");
    setAdminReleaseBlocked(false);
    setIsDragging(false);
    setShowZipModal(true);
    if (canAdminUploadSkills) {
      void refreshAdminSkillCatalog();
    }
  };

  const processZipFile = (file: File) => {
    if (!file.name.endsWith(".zip")) {
      toast.error(t("skills.invalidZipFile"));
      return;
    }
    setZipFile(file);
    setZipSkills([]);
    setSelectedZipSkills([]);
    setAdminReleasePhase("idle");
    setAdminReleaseBlocked(false);
    handleZipPreviewWithFile(file);
  };

  const handleZipFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0] || null;
    if (file) processZipFile(file);
  };

  const handleZipPreviewWithFile = async (file: File) => {
    setZipPreviewing(true);
    setZipSkills([]);
    setSelectedZipSkills([]);
    try {
      const result = canAdminUploadSkills
        ? await adminPreviewZipSkills(file)
        : await previewZipSkills(file);
      if (result && result.skills) {
        setZipSkills(result.skills);
        setSelectedZipSkills(
          initialZipSkillSelection(result.skills, canAdminUploadSkills),
        );
      }
    } finally {
      setZipPreviewing(false);
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    const file = e.dataTransfer.files?.[0] || null;
    if (file) processZipFile(file);
  };

  const handleZipSkillToggle = (name: string) => {
    setAdminReleasePhase("idle");
    setAdminReleaseBlocked(false);
    setSelectedZipSkills((prev) =>
      toggleZipSkillSelection(prev, name, zipSkills, canAdminUploadSkills),
    );
  };

  const handleZipSelectAll = (names: string[]) => {
    setAdminReleasePhase("idle");
    setAdminReleaseBlocked(false);
    setSelectedZipSkills(
      coerceZipSkillSelection(names, zipSkills, canAdminUploadSkills),
    );
  };

  const handleZipUpload = async () => {
    if (!zipFile || selectedZipSkills.length === 0) return;
    setZipUploading(true);
    try {
      if (canAdminUploadSkills) {
        if (selectedZipSkills.length !== 1) {
          toast.error(t("skills.adminUploadSelectOne"));
          return;
        }
        const selectedSkill = zipSkills.find(
          (skill) => skill.name === selectedZipSkills[0],
        );
        if (!selectedSkill) {
          toast.error(t("skills.adminUploadSelectOne"));
          return;
        }
        setAdminReleaseBlocked(false);
        setAdminReleasePhase("uploading");
        const uploaded = await adminUploadSkill(zipFile, selectedSkill.name);
        let nextReleaseAction = adminReleaseActionForStatus(
          uploaded?.uploaded.status ?? "",
        );
        if (
          !uploaded ||
          uploaded.uploaded.skillId !== selectedSkill.name ||
          nextReleaseAction === "blocked"
        ) {
          setAdminReleaseBlocked(true);
          toast.error(t("skills.adminReleaseDraftFailed"));
          return;
        }

        const { skillId, version } = uploaded.uploaded;
        const uploadedCatalog = await refreshAdminSkillCatalog();
        const uploadedCatalogItem = uploadedCatalog?.find(
          (item) => item.skillId === skillId,
        );
        if (
          !uploadedCatalogItem ||
          uploadedCatalogItem.latestVersion !== version ||
          uploadedCatalogItem.latestVersionStatus !== uploaded.uploaded.status
        ) {
          setAdminReleaseBlocked(true);
          toast.error(t("skills.adminReleaseDraftFailed"));
          return;
        }
        if (nextReleaseAction === "review") {
          setAdminReleasePhase("reviewing");
          const reviewed = await adminReviewSkillVersion(skillId, version);
          if (
            !reviewed ||
            reviewed.skillId !== skillId ||
            reviewed.version !== version ||
            reviewed.status !== "reviewed"
          ) {
            setAdminReleaseBlocked(true);
            toast.error(t("skills.adminReleaseReviewFailed"));
            return;
          }
          nextReleaseAction = adminReleaseActionForStatus(reviewed.status);
          const reviewedCatalog = await refreshAdminSkillCatalog();
          const reviewedCatalogItem = reviewedCatalog?.find(
            (item) => item.skillId === skillId,
          );
          if (
            !reviewedCatalogItem ||
            reviewedCatalogItem.latestVersion !== version ||
            reviewedCatalogItem.latestVersionStatus !== "reviewed"
          ) {
            setAdminReleaseBlocked(true);
            toast.error(t("skills.adminReleaseReviewFailed"));
            return;
          }
        }

        if (nextReleaseAction === "promote") {
          setAdminReleasePhase("promoting");
          const release = await adminPromoteSkillVersion(skillId, version);
          if (
            !release ||
            release.skillId !== skillId ||
            release.currentVersion !== version ||
            release.channel !== "stable" ||
            release.rolloutPercent !== 100 ||
            release.status !== "active"
          ) {
            setAdminReleaseBlocked(true);
            toast.error(t("skills.adminReleasePromoteFailed"));
            return;
          }
          nextReleaseAction = "refresh";
        }
        if (nextReleaseAction !== "refresh") {
          setAdminReleaseBlocked(true);
          toast.error(t("skills.adminReleasePromoteFailed"));
          return;
        }

        setAdminReleasePhase("refreshing");
        const [publicCatalogRefreshed, adminCatalog] = await Promise.all([
          fetchSkills(),
          refreshAdminSkillCatalog(),
        ]);
        const releasedCatalogItem = adminCatalog?.find(
          (item) => item.skillId === skillId,
        );
        if (
          !publicCatalogRefreshed ||
          !releasedCatalogItem ||
          releasedCatalogItem.latestVersion !== version ||
          releasedCatalogItem.latestVersionStatus !== "released" ||
          releasedCatalogItem.currentVersion !== version ||
          releasedCatalogItem.rolloutPercent !== 100 ||
          releasedCatalogItem.distributionStatus !== "active" ||
          !releasedCatalogItem.visibleToUser
        ) {
          setAdminReleaseBlocked(true);
          toast.error(t("skills.adminReleaseRefreshFailed"));
          return;
        }
        toast.success(t("skills.adminReleaseSuccess"));
        setShowZipModal(false);
        setZipFile(null);
        setZipSkills([]);
        setSelectedZipSkills([]);
        setAdminReleasePhase("idle");
        return;
      }
      const result = await uploadSkill(zipFile, selectedZipSkills);
      if (result && result.created.length > 0) {
        setShowZipModal(false);
        setZipFile(null);
        setZipSkills([]);
        setSelectedZipSkills([]);
      }
    } finally {
      setZipUploading(false);
    }
  };

  // GitHub import handlers
  const handleGithubClick = () => {
    setGithubUrl("");
    setGithubBranch("main");
    setGithubSkills([]);
    setSelectedGithubSkills([]);
    setShowGithubModal(true);
  };

  const handleGithubPreview = async () => {
    if (!githubUrl.trim()) return;
    setGithubLoading(true);
    setGithubSkills([]);
    setSelectedGithubSkills([]);
    try {
      const result = await previewGitHubSkills(githubUrl, githubBranch);
      if (result && result.skills) {
        setGithubSkills(result.skills);
      }
    } finally {
      setGithubLoading(false);
    }
  };

  const handleGithubSkillToggle = (name: string) => {
    setSelectedGithubSkills((prev) =>
      prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name],
    );
  };

  const handleGithubInstall = async () => {
    if (selectedGithubSkills.length === 0) return;
    setGithubInstalling(true);
    try {
      const result = await installGitHubSkills(
        githubUrl,
        selectedGithubSkills,
        githubBranch,
      );
      if (result) {
        setShowGithubModal(false);
        setGithubSkills([]);
        setSelectedGithubSkills([]);
      }
    } finally {
      setGithubInstalling(false);
    }
  };

  const handleGithubExport = async () => {
    if (selectedGithubSkills.length === 0) return;
    setGithubExporting(true);
    try {
      const result = await installGitHubSkills(
        githubUrl,
        selectedGithubSkills,
        githubBranch,
      );
      if (!result?.installed?.length) {
        toast.error(t("skills.exportFailed"));
        return;
      }
      const installedSkill = await getFullSkill(result.installed[0]);
      if (!installedSkill) {
        toast.error(t("skills.exportFailed"));
        return;
      }
      await exportProjectZip(installedSkill.files, installedSkill.name);
      toast.success(t("skills.exportSuccess"));
    } catch {
      toast.error(t("skills.exportFailed"));
    } finally {
      setGithubExporting(false);
    }
  };

  return {
    // Data
    skills,
    isLoading,
    error,
    listError,
    filteredSkills,
    paginatedSkills,
    availableTags,
    effectivePermissions,
    effectivePermissionsKnown,
    catalogReadResolved,
    total,
    page,
    pageSize,

    // Search & filter
    searchQuery,
    setSearchQuery,
    selectedTags,
    isFilterOpen,
    setIsFilterOpen,
    toggleTag,
    clearFilters,
    setPage,

    // Form modal
    editingSkill,
    isCreating,
    showModal,
    handleCreate,
    handleEdit,
    handleSave,
    handleCancel,

    // CRUD
    handleExportZip,
    handleDelete,
    handleToggle,
    clearError,

    // Delete confirm
    isDeleteConfirmOpen,
    deleteConfirmData,
    confirmDelete,
    cancelDelete,

    // Publish
    publishConfirm,
    setPublishConfirm,
    confirmPublish,

    // Batch
    selectedNames,
    selectionMode,
    batchLoading,
    handleSelectSkill,
    handleSelectAll,
    clearSelection,
    handleBatchDelete,
    handleBatchToggle,

    // ZIP upload
    showZipModal,
    setShowZipModal,
    zipFile,
    zipUploading,
    zipPreviewing,
    zipSkills,
    selectedZipSkills,
    adminReleasePhase,
    adminReleaseBlocked,
    adminCatalogItems,
    zipInputRef,
    isDragging,
    handleZipClick,
    handleZipFileChange,
    handleDragOver,
    handleDragLeave,
    handleDrop,
    handleZipSkillToggle,
    handleZipSelectAll,
    handleZipUpload,
    canAdminUploadSkills,

    // GitHub import
    showGithubModal,
    setShowGithubModal,
    githubUrl,
    setGithubUrl,
    githubBranch,
    setGithubBranch,
    githubSkills,
    selectedGithubSkills,
    githubLoading,
    githubInstalling,
    githubExporting,
    handleGithubClick,
    handleGithubPreview,
    handleGithubSkillToggle,
    setSelectedGithubSkills,
    handleGithubInstall,
    handleGithubExport,
  };
}
