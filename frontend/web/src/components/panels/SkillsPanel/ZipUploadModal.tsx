import { useTranslation } from "react-i18next";
import { Archive, UploadCloud, FileArchive, Upload } from "lucide-react";
import { LoadingSpinner } from "../../common/LoadingSpinner";
import { EditorSidebar } from "../../common/EditorSidebar";
import { Checkbox } from "../../common/Checkbox";
import type { AdminSkillCatalogItem } from "../../../services/api/skill";
import { canSelectZipSkill, type ZipSkillPreview } from "./zipSelection";
import type { AdminSkillReleasePhase } from "./useSkillsActions";

interface ZipUploadModalProps {
  showZipModal: boolean;
  setShowZipModal: (show: boolean) => void;
  zipFile: File | null;
  zipUploading: boolean;
  zipPreviewing: boolean;
  zipSkills: ZipSkillPreview[];
  selectedZipSkills: string[];
  adminRelease: boolean;
  adminReleasePhase: AdminSkillReleasePhase;
  adminReleaseBlocked: boolean;
  adminCatalogItems: AdminSkillCatalogItem[];
  zipInputRef: React.RefObject<HTMLInputElement | null>;
  isDragging: boolean;
  onZipFileChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onDragOver: (e: React.DragEvent) => void;
  onDragLeave: (e: React.DragEvent) => void;
  onDrop: (e: React.DragEvent) => void;
  onZipSkillToggle: (name: string) => void;
  onZipSelectAll: (names: string[]) => void;
  onZipUpload: () => void;
}

export function ZipUploadModal({
  showZipModal,
  setShowZipModal,
  zipFile,
  zipUploading,
  zipPreviewing,
  zipSkills,
  selectedZipSkills,
  adminRelease,
  adminReleasePhase,
  adminReleaseBlocked,
  adminCatalogItems,
  zipInputRef,
  isDragging,
  onZipFileChange,
  onDragOver,
  onDragLeave,
  onDrop,
  onZipSkillToggle,
  onZipSelectAll,
  onZipUpload,
}: ZipUploadModalProps) {
  const { t } = useTranslation();

  const backedCount = zipSkills.filter((s) => s.already_exists).length;
  const selectableCount = adminRelease
    ? Math.min(zipSkills.length, 1)
    : backedCount;
  const lifecycleStages: Array<{
    phase: Exclude<AdminSkillReleasePhase, "idle">;
    label: string;
  }> = [
    { phase: "uploading", label: t("skills.adminReleaseDraftStep") },
    { phase: "reviewing", label: t("skills.adminReleaseReviewStep") },
    { phase: "promoting", label: t("skills.adminReleasePromoteStep") },
    { phase: "refreshing", label: t("skills.adminReleaseRefreshStep") },
  ];
  const currentStageIndex = lifecycleStages.findIndex(
    (stage) => stage.phase === adminReleasePhase,
  );
  const selectedAdminCatalogItem = adminRelease
    ? adminCatalogItems.find((item) => item.skillId === selectedZipSkills[0])
    : undefined;

  return (
    <EditorSidebar
      open={showZipModal}
      onClose={() => setShowZipModal(false)}
      title={
        adminRelease
          ? t("skills.adminReleaseZipTitle")
          : t("skills.uploadZipTitle")
      }
      subtitle={
        adminRelease ? t("skills.adminReleaseZipSubtitle") : t("skills.subtitle")
      }
      icon={<Archive size={16} />}
      width="wide"
      footer={
        <div className="flex justify-end gap-2">
          <button
            onClick={() => setShowZipModal(false)}
            disabled={zipUploading || zipPreviewing}
            className="btn-secondary disabled:opacity-50"
          >
            {t("common.cancel")}
          </button>
          {zipSkills.length > 0 && (
            <button
              onClick={onZipUpload}
              disabled={zipUploading || selectedZipSkills.length === 0}
              className="btn-primary disabled:opacity-50"
            >
              {zipUploading ? (
                <LoadingSpinner size="sm" color="text-white" />
              ) : (
                <Upload size={16} />
              )}
              <span className="hidden sm:inline">
                {adminRelease
                  ? t("skills.adminReleaseAction")
                  : t("skills.importSkills")} ({selectedZipSkills.length})
              </span>
            </button>
          )}
        </div>
      }
    >
      <div data-disable-global-file-drop="true" className="es-form">
        {/* Drag & Drop / Click Upload Zone */}
        <div
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
          onClick={(e) => {
            e.stopPropagation();
            zipInputRef.current?.click();
          }}
          className={`group relative flex cursor-pointer flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed px-6 py-10 transition-all duration-200 ${
            isDragging
              ? "border-[var(--theme-primary)] bg-[var(--theme-primary-light)]/40 scale-[1.01]"
              : "border-[var(--theme-border)] bg-[var(--theme-bg)]/60 hover:border-[var(--theme-primary)]/50 hover:bg-[var(--theme-bg)]/90"
          } ${zipPreviewing ? "pointer-events-none opacity-60" : ""}`}
        >
          <input
            ref={zipInputRef}
            type="file"
            accept=".zip"
            onChange={onZipFileChange}
            className="hidden"
          />
          <div
            className={`flex h-14 w-14 items-center justify-center rounded-lg transition-all duration-200 ${
              isDragging
                ? "bg-[var(--theme-primary)] text-white shadow-lg shadow-[var(--theme-primary)]/20 scale-110"
                : "bg-[var(--theme-primary-light)] text-[var(--theme-primary)] group-hover:scale-105"
            }`}
          >
            {isDragging ? <FileArchive size={24} /> : <UploadCloud size={24} />}
          </div>
          <div className="text-center">
            <p className="text-sm font-medium text-[var(--theme-text)]">
              {isDragging
                ? t("skills.dropZoneActive")
                : t("skills.dropZoneTitle")}
            </p>
            <p className="mt-1 text-xs text-[var(--theme-text-secondary)]">
              {t("skills.dropZoneHint")}
            </p>
          </div>
          {zipFile && (
            <div className="flex items-center gap-2 rounded-lg bg-[var(--theme-primary-light)]/60 px-3 py-1.5">
              <Archive
                size={14}
                className="text-[var(--theme-primary)] shrink-0"
              />
              <span className="text-xs font-medium text-[var(--theme-text)] truncate max-w-[200px]">
                {zipFile.name}
              </span>
              <span className="text-xs text-[var(--theme-text-secondary)]">
                ({(zipFile.size / 1024).toFixed(1)} KB)
              </span>
            </div>
          )}
        </div>

        {zipPreviewing && (
          <div className="flex items-center justify-center gap-2 py-3 text-sm text-[var(--theme-text-secondary)]">
            <LoadingSpinner size="sm" />
            {t("skills.preview")}
          </div>
        )}

        {adminRelease && zipFile && (
          <div className="rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-3 py-3">
            <p className="text-sm font-medium text-[var(--theme-text)]">
              {t("skills.adminReleaseLifecycleTitle")}
            </p>
            <p className="mt-1 text-xs leading-5 text-[var(--theme-text-secondary)]">
              {t("skills.adminReleaseLifecycleHint")}
            </p>
            <ol className="mt-3 space-y-2">
              {lifecycleStages.map((stage, index) => {
                const isCurrent = currentStageIndex === index;
                const isComplete = currentStageIndex > index;
                return (
                  <li
                    key={stage.phase}
                    className="flex items-center gap-2 text-xs text-[var(--theme-text-secondary)]"
                  >
                    <span
                      className={`inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[10px] font-semibold ${
                        isComplete
                          ? "border-[var(--theme-primary)] bg-[var(--theme-primary)] text-white"
                          : isCurrent
                            ? "border-[var(--theme-primary)] text-[var(--theme-primary)]"
                            : "border-[var(--theme-border)]"
                      }`}
                    >
                      {isComplete ? "✓" : index + 1}
                    </span>
                    <span
                      className={
                        isCurrent
                          ? "font-medium text-[var(--theme-text)]"
                          : undefined
                      }
                    >
                      {stage.label}
                    </span>
                  </li>
                );
              })}
            </ol>
            {adminReleaseBlocked && (
              <p className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-2.5 py-2 text-xs leading-5 text-amber-800 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-200">
                {t("skills.adminReleaseBlocked")}
              </p>
            )}
            {selectedAdminCatalogItem?.latestVersionStatus === "draft" && (
              <p className="mt-3 rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg)] px-2.5 py-2 text-xs leading-5 text-[var(--theme-text-secondary)]">
                {t("skills.adminReleaseCatalogDraft")}
              </p>
            )}
            {selectedAdminCatalogItem?.latestVersionStatus === "reviewed" && (
              <p className="mt-3 rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg)] px-2.5 py-2 text-xs leading-5 text-[var(--theme-text-secondary)]">
                {t("skills.adminReleaseCatalogReviewed")}
              </p>
            )}
          </div>
        )}

        {zipSkills.length > 0 && (
          <div className="es-section space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <label className="text-sm font-medium text-[var(--theme-text)]">
                  {adminRelease
                    ? t("skills.selectSkillToRelease")
                    : t("skills.selectBackedZipSkills")}
                </label>
                <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-[var(--theme-primary)]/10 px-1.5 text-[11px] font-semibold text-[var(--theme-primary)]">
                  {selectedZipSkills.length}/{selectableCount}
                </span>
              </div>
              {!adminRelease && (
                <button
                  onClick={() => {
                    const selectable = zipSkills
                      .filter((s) => s.already_exists)
                      .map((s) => s.name);
                    onZipSelectAll(
                      selectedZipSkills.length === selectable.length
                        ? []
                        : selectable,
                    );
                  }}
                  className="rounded-md px-2 py-1 text-xs font-medium text-[var(--theme-primary)] transition-colors hover:bg-[var(--theme-primary)]/8"
                >
                  {selectedZipSkills.length === selectableCount
                    ? t("common.deselectAll")
                    : t("common.selectAll")}
                </button>
              )}
            </div>
            <p className="text-xs leading-5 text-[var(--theme-text-secondary)]">
              {adminRelease
                ? t("skills.adminReleaseZipHint")
                : t("skills.zipImportBackedHint")}
            </p>
            <div className="space-y-1.5 max-h-72 overflow-y-auto rounded-lg p-1">
              {zipSkills.map((skill) => {
                const selected = selectedZipSkills.includes(skill.name);
                const canSelectSkill = canSelectZipSkill(skill, adminRelease);
                return (
                  <div
                    key={skill.name}
                    onClick={() =>
                      canSelectSkill && onZipSkillToggle(skill.name)
                    }
                    className={`group flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2.5 transition-all duration-150 ${
                      !canSelectSkill
                        ? "cursor-not-allowed opacity-40"
                        : selected
                          ? "bg-[var(--theme-primary)]/8"
                          : "hover:bg-[var(--theme-primary)]/4"
                    }`}
                  >
                    <Checkbox
                      size="sm"
                      checked={selected}
                      onChange={() =>
                        canSelectSkill && onZipSkillToggle(skill.name)
                      }
                      disabled={!canSelectSkill}
                    />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <p
                          className={`text-sm font-medium truncate transition-colors ${
                            selected
                              ? "text-[var(--theme-primary)]"
                              : "text-[var(--theme-text)]"
                          }`}
                        >
                          {skill.name}
                        </p>
                        {skill.already_exists && adminRelease && (
                          <span className="shrink-0 rounded-full bg-[var(--theme-primary)]/8 px-1.5 py-0.5 text-[10px] font-medium text-[var(--theme-primary)]/70">
                            {t("skills.adminReleaseExistingSkill")}
                          </span>
                        )}
                        {skill.already_exists && !adminRelease && (
                          <span className="shrink-0 rounded-full bg-[var(--theme-primary)]/8 px-1.5 py-0.5 text-[10px] font-medium text-[var(--theme-primary)]/70">
                            {t("skills.publicCatalogSkill")}
                          </span>
                        )}
                        {!skill.already_exists && adminRelease && (
                          <span className="shrink-0 rounded-full bg-emerald-50 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300">
                            {t("skills.newSkillAdminUpload")}
                          </span>
                        )}
                        {!skill.already_exists && !adminRelease && (
                          <span className="shrink-0 rounded-full bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:bg-amber-950/40 dark:text-amber-300">
                            {t("skills.newSkillImportUnsupported")}
                          </span>
                        )}
                        {!skill.already_exists && skill.file_count > 1 && (
                          <span className="shrink-0 text-[10px] text-[var(--theme-text-secondary)]">
                            {skill.file_count} files
                          </span>
                        )}
                      </div>
                      {skill.description && (
                        <p className="mt-0.5 text-xs text-[var(--theme-text-secondary)] truncate">
                          {skill.description}
                        </p>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </EditorSidebar>
  );
}
