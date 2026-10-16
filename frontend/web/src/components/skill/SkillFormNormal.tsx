import { Button, Input, TextArea } from "@lobehub/ui/base-ui";
import { useId } from "react";
import { useTranslation } from "react-i18next";
import { Maximize2, X, Plus, Save, Tag, Pencil } from "lucide-react";
import { Switch } from "@lobehub/ui/base-ui";
import { Spin } from "@lobehub/ui/base-ui";
import { FileTabs } from "./FileTabs";
import { SkillEditor } from "./SkillEditor";
import { BinaryFilePreview } from "./BinaryFilePreview";
import { normalizeTags } from "./SkillForm.utils";
import type { SkillFormActions } from "./SkillForm.types";

export function SkillFormNormal(a: SkillFormActions) {
  const { t } = useTranslation();
  const editorPanelId = useId();
  const submitLabel = a.isEditing
    ? t("skills.form.saveChanges")
    : t("skills.form.createSkill");

  return (
    <>
      <div className="flex flex-1 flex-col gap-4">
        {/* Metadata card */}
        <div className="skill-form-card rounded-lg shadow-sm">
          <div className="space-y-4 px-4 py-4 sm:px-5">
            {/* Name */}
            <div className="space-y-1.5">
              <label className="block text-xs font-medium text-[var(--theme-text-secondary)]">
                {t("skills.form.name")}
              </label>
              <Input
                type="text" size="large" aria-label={t("skills.form.name")}
                aria-invalid={Boolean(a.errors.name)}
                value={a.name} disabled={a.isEditing}
                onChange={(event) => a.setName(event.target.value)}
                placeholder={t("skills.form.namePlaceholder")}
                className="w-full" classNames={{ input: "font-mono" }}
              />
              {a.errors.name && (
                <p className="text-xs text-red-500">{a.errors.name}</p>
              )}
              {a.isEditing && !a.errors.name && (
                <p className="text-xs text-stone-400 dark:text-stone-500">
                  {t("skills.form.nameCannotChange")}
                </p>
              )}
            </div>

            {/* Description */}
            <div className="space-y-1.5">
              <label className="block text-xs font-medium text-[var(--theme-text-secondary)]">
                {t("skills.form.description")}
              </label>
              <TextArea
                value={a.description}
                onChange={(e) => a.setDescription(e.target.value)}
                placeholder={t("skills.form.descriptionPlaceholder")}
                rows={3}
                aria-label={t("skills.form.description")} aria-invalid={Boolean(a.errors.description)}
                className="w-full" classNames={{ input: "leading-6" }}
              />
              {a.errors.description && (
                <p className="text-xs text-red-500">{a.errors.description}</p>
              )}
            </div>

            {/* Tags */}
            <div className="space-y-1.5">
              <label className="block text-xs font-medium text-[var(--theme-text-secondary)]">
                {t("adminMarketplace.tags")}
              </label>
              <div className="skill-tag-editor rounded-lg bg-[var(--theme-bg)] p-3 shadow-sm">
                <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--theme-text-secondary)]/80">
                  <Tag size={12} className="text-[var(--theme-primary)]" />
                  {t("adminMarketplace.tags")}
                </div>
                <p className="mt-2 text-xs leading-5 text-[var(--theme-text-secondary)]/80">
                  {t("adminMarketplace.tagsHint")}
                </p>
                <Input
                  type="text"
                  value={a.tagsInput}
                  onChange={(e) => a.setTagsInput(e.target.value)}
                  placeholder={t("adminMarketplace.tagsPlaceholder")}
                  size="large" aria-label={t("adminMarketplace.tags")} aria-invalid={Boolean(a.errors.tags)}
                  className="mt-3 w-full"
                />
                <div className="mt-3 flex flex-wrap gap-2">
                  {normalizeTags(a.tagsInput).map((tag) => (
                    <span
                      key={tag}
                      className="skill-tag-chip skill-tag-chip--active"
                    >
                      {tag}
                      <Button type="text"
                        htmlType="button"
                        onClick={() => a.removeTag(tag)}
                        size="small" icon={<X size={11} />}
                        aria-label={`Remove tag ${tag}`}
                      />
                    </span>
                  ))}
                  {normalizeTags(a.tagsInput).length === 0 && (
                    <span className="text-xs text-[var(--theme-text-secondary)]/80">
                      {t("adminMarketplace.tagsPlaceholder")}
                    </span>
                  )}
                </div>
              </div>
              {a.errors.tags && (
                <p className="text-xs text-red-500">{a.errors.tags}</p>
              )}
            </div>

            {/* Enabled toggle */}
            <div className="skill-toggle-panel flex items-center justify-between rounded-lg bg-[var(--theme-bg)] px-3 py-3">
              <div className="min-w-0 pr-3">
                <p className="text-sm font-medium text-[var(--theme-text)]">
                  {t("skills.form.enabled")}
                </p>
                <p className="mt-1 text-xs text-[var(--theme-text-secondary)]">
                  {a.enabled
                    ? t("skills.form.enabledHint")
                    : t("skills.form.disabledHint")}
                </p>
              </div>
              <div className="shrink-0">
                <label className="inline-flex items-center gap-2.5">
                  <Switch checked={a.enabled} onChange={a.setEnabled} />
                  <span className="text-sm">{t("skills.form.enabled")}</span>
                </label>
              </div>
            </div>
          </div>
        </div>

        {/* Editor area */}
        <div className="skill-form-editor flex flex-col overflow-hidden rounded-lg shadow-sm">
          <div className="shrink-0 px-3 py-3 sm:px-4">
            <div className="flex flex-col gap-3">
              <div className="flex items-center justify-between gap-3">
                <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--theme-text-secondary)]/80">
                  {t("skills.form.files", "Files")}
                </p>
                <div className="flex items-center gap-1 shrink-0">
                  <Button type="text"
                    htmlType="button"
                    onClick={a.addFile}
                    icon={<Plus size={15} />}
                    aria-label={t("skills.form.addFile")}
                    title={t("skills.form.addFile")}
                  />
                  <Button type="text"
                    htmlType="button"
                    onClick={() => a.toggleFullscreen(true)}
                    icon={<Maximize2 size={15} />}
                    aria-label={t("skills.form.fullscreenEditor")}
                    title={t("skills.form.fullscreenEditor")}
                  />
                </div>
              </div>

              <div className="skill-file-tabs min-w-0 overflow-hidden rounded-lg px-1 py-1">
                <FileTabs
                  panelId={editorPanelId}
                  files={a.files}
                  activeFileIndex={a.activeFileIndex}
                  onSelect={a.setActiveFileIndex}
                  onRemove={a.removeFile}
                  untitledLabel={t("skills.form.untitled")}
                />
              </div>

              <div className="skill-file-path rounded-lg px-3 py-2.5">
                <label className="mb-1 block text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--theme-text-secondary)]/80">
                  {t("skills.form.filePath")}
                </label>
                <Input
                  type="text"
                  value={a.files[a.activeFileIndex]?.path || ""}
                  readOnly={!a.files[a.activeFileIndex]?.loaded || a.files[a.activeFileIndex]?.binary}
                  onChange={(e) =>
                    a.updateFilePath(a.activeFileIndex, e.target.value)
                  }
                  placeholder={t("skills.form.filePathPlaceholder")}
                  variant="borderless" size="small" aria-label={t("skills.form.filePath")}
                  className="w-full" classNames={{ input: "font-mono text-xs" }}
                />
              </div>
            </div>
          </div>

          {/* Editor / Binary Preview */}
          <div id={editorPanelId} role="tabpanel" aria-labelledby={`${editorPanelId}-tab-${a.activeFileIndex}`} className="flex-1 min-h-0 p-3 sm:p-4">
            {(() => {
              const currentPath = a.files[a.activeFileIndex]?.path || "";
              const binaryInfo = a.binaryFiles?.[currentPath];

              // Loading state
              if (a.loadingFilePath === currentPath) {
                return (
                  <div className="flex h-full min-h-[18rem] sm:min-h-[24rem] items-center justify-center rounded-lg bg-[var(--theme-bg)]">
                    <div className="flex flex-col items-center gap-3">
                      <Spin size={24} aria-label={t("common.loading")} />
                      <span className="text-sm text-[var(--theme-text-secondary)]">
                        {currentPath.split("/").pop()}
                      </span>
                    </div>
                  </div>
                );
              }

              if (binaryInfo) {
                return (
                  <BinaryFilePreview
                    url={binaryInfo.url}
                    mime_type={binaryInfo.mime_type}
                    size={binaryInfo.size}
                    fileName={currentPath.split("/").pop() || currentPath}
                  />
                );
              }
              return (
                <div
                  className={`relative flex flex-col overflow-hidden rounded-lg bg-[var(--theme-bg)] transition-colors duration-150 ${
                    a.errors.content
                      ? "ring-1 ring-red-300 dark:ring-red-700"
                      : ""
                  }`}
                  style={{ height: "180px" }}
                >
                  <SkillEditor
                    value={a.files[a.activeFileIndex]?.content || ""}
                    onChange={(val) =>
                      a.updateFileContent(a.activeFileIndex, val)
                    }
                    className="h-full"
                    filePath={a.files[a.activeFileIndex]?.path}
                    readOnly
                  />
                  <Button type="text"
                    htmlType="button"
                    onClick={() => a.toggleFullscreen(true)}
                    className="absolute right-3 bottom-3 flex items-center gap-1.5 rounded-lg bg-[var(--theme-primary)] px-2.5 py-1.5 text-xs font-medium text-white shadow-md transition-all duration-150 hover:scale-105 active:scale-95"
                  >
                    <Pencil size={12} />
                    {t("skills.form.editFullscreen", "Edit")}
                  </Button>
                </div>
              );
            })()}
            {(a.errors.content || a.errors.files) && (
              <p className="mt-2 text-xs text-red-500">
                {a.errors.content || a.errors.files}
              </p>
            )}
          </div>
        </div>
      </div>

      {/* Bottom action bar */}
      <div className="skill-action-bar shrink-0 flex items-center justify-end gap-2 px-1 pt-3">
        <Button type="text"
          htmlType="button"
          onClick={a.onCancel}
          disabled={a.isLoading}
          className="rounded-lg px-4 py-2 text-sm text-[var(--theme-text)] hover:bg-stone-50 dark:hover:bg-stone-800 disabled:opacity-50 transition-colors duration-150"
        >
          {t("common.cancel")}
        </Button>
        <Button type="text"
          htmlType="submit"
          disabled={a.isLoading}
          className="rounded-lg bg-[var(--theme-primary)] px-5 py-2 text-sm font-medium text-white hover:bg-[var(--theme-primary-hover)] disabled:opacity-50 transition-colors duration-150 dark:text-stone-950 inline-flex items-center gap-2"
        >
          <span className="inline-flex h-4 w-4 items-center justify-center">
            {a.isLoading ? <Spin size={16} /> : <Save size={16} />}
          </span>
          <span className={a.isLoading ? "loading-text" : ""}>
            {submitLabel}
          </span>
        </Button>
      </div>
    </>
  );
}
