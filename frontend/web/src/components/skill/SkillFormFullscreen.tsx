import { Button, Spin } from "@lobehub/ui/base-ui";
import { useId } from "react";
import { useTranslation } from "react-i18next";
import { Shrink, Plus, ChevronDown } from "lucide-react";
import { FileTreeItem } from "./FileTreeItem";
import { FileTabs } from "./FileTabs";
import { SkillEditor } from "./SkillEditor";
import { BinaryFilePreview } from "./BinaryFilePreview";
import { buildFileTree } from "./SkillForm.utils";
import type { SkillFormActions } from "./SkillForm.types";

export function SkillFormFullscreen(a: SkillFormActions) {
  const { t } = useTranslation();
  const editorPanelId = useId();

  return (
    <>
      {/* Top-right floating exit button — always visible, prominent */}
      <Button type="primary" size="large"
        htmlType="button" icon={<Shrink size={18} />}
        onClick={() => a.toggleFullscreen(false)}
        className="fixed top-4 right-4 z-[410] shadow-xl"
        aria-label={t("skills.form.exitFullscreen")} title={t("skills.form.exitFullscreen")}
      />
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* Editor area — sidebar + editor, full-screen focused */}
        <div className="skill-form-editor flex flex-1 min-h-0 overflow-hidden">
          {/* Desktop sidebar */}
          <div className="skill-file-sidebar hidden w-52 shrink-0 flex-col sm:flex lg:w-60">
            <div className="flex items-center justify-between px-3 py-1.5">
              <div className="flex items-center gap-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--theme-text-secondary)]/80 select-none">
                <ChevronDown size={12} />
                {t("skills.form.files", "Files")}
              </div>
            </div>
            <div className="flex-1 overflow-y-auto py-0.5">
              {buildFileTree(a.files).map((node, i) => (
                <FileTreeItem
                  key={i}
                  node={node}
                  depth={0}
                  activeFileIndex={a.activeFileIndex}
                  onSelect={a.setActiveFileIndex}
                  onRemove={a.removeFile}
                  canRemove={a.files.length > 1}
                />
              ))}
            </div>
            <div className="shrink-0 px-2 py-1.5">
              <Button type="text"
                htmlType="button"
                onClick={a.addFile}
                className="w-full flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-sm text-stone-500 dark:text-stone-400 hover:bg-stone-100 dark:hover:bg-white/5 transition-colors"
              >
                <Plus size={13} />
                {t("skills.form.addFile")}
              </Button>
            </div>
          </div>

          {/* Right: editor only */}
          <div className="flex flex-1 flex-col min-h-0">
            {/* Mobile header: tabs + actions */}
            <div className="shrink-0 flex items-center gap-1 px-3 py-2 sm:hidden">
              <div className="flex-1 min-w-0">
                <FileTabs
                  panelId={editorPanelId}
                  files={a.files}
                  activeFileIndex={a.activeFileIndex}
                  onSelect={a.setActiveFileIndex}
                  onRemove={a.removeFile}
                  untitledLabel={t("skills.form.untitled")}
                />
              </div>
              <Button type="text"
                htmlType="button"
                onClick={a.addFile}
                className="shrink-0" icon={<Plus size={15} />}
                aria-label={t("skills.form.addFile")} title={t("skills.form.addFile", "Add file")}
              />
            </div>

            {/* Editor / Binary Preview */}
            <div id={editorPanelId} role="tabpanel" aria-labelledby={`${editorPanelId}-tab-${a.activeFileIndex}`} className="flex-1 min-h-0 p-3 sm:p-4">
              {(() => {
                const currentPath = a.files[a.activeFileIndex]?.path || "";
                const binaryInfo = a.binaryFiles?.[currentPath];

                // Loading state
                if (a.loadingFilePath === currentPath) {
                  return (
                    <div className="flex h-full items-center justify-center rounded-lg bg-[var(--theme-bg)]">
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
                    className={`flex h-full flex-col overflow-hidden rounded-lg bg-[var(--theme-bg)] transition-colors duration-150 ${
                      a.errors.content
                        ? "ring-1 ring-red-300 dark:ring-red-700"
                        : ""
                    } skill-editor-shell`}
                  >
                    <SkillEditor
                      value={a.files[a.activeFileIndex]?.content || ""}
                      onChange={(val) =>
                        a.updateFileContent(a.activeFileIndex, val)
                      }
                      className="flex-1 min-h-0"
                      filePath={a.files[a.activeFileIndex]?.path}
                      readOnly={!a.files[a.activeFileIndex]?.loaded || a.files[a.activeFileIndex]?.binary}
                    />
                  </div>
                );
              })()}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
