import { useTranslation } from "react-i18next";
import { MoreHorizontal } from "lucide-react";
import type { RevealedFileItem } from "../../../services/api";
import { getFileTypeInfo } from "../../documents/utils";
import { useContextMenu } from "../hooks/useContextMenu";
import { buildFileCardPreview, buildMeta } from "../utils";
import { FileContextMenu } from "./FileContextMenu";
import { FileCardPreview } from "./FileCardPreview";

interface GridCardProps {
  file: RevealedFileItem;
  onPreview: (file: RevealedFileItem) => void;
  onGoToSession: (sessionId: string, file?: RevealedFileItem) => void;
  onToggleFavorite: (file: RevealedFileItem) => void;
}

export function GridCard({
  file,
  onPreview,
  onGoToSession,
  onToggleFavorite,
}: GridCardProps) {
  const { t } = useTranslation();
  const fileInfo = getFileTypeInfo(file.file_name, file.mime_type || undefined);
  const FileIcon = fileInfo.icon;
  const isProject = file.file_type === "project";
  const cardPreview = buildFileCardPreview(file);
  const meta = buildMeta(file, t);
  const ctx = useContextMenu();

  return (
    <>
      <div
        onClick={() => onPreview(file)}
        onContextMenu={(e) => ctx.show(e, file)}
        className="group/card relative flex cursor-pointer flex-col overflow-hidden rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] shadow-[0_1px_2px_rgba(18,38,63,0.04)] transition-all duration-200 hover:border-[var(--theme-border-strong)] hover:shadow-[0_8px_18px_rgba(18,38,63,0.05)]"
      >
        {/* File header */}
        <div className="flex items-center gap-2 border-b border-[var(--theme-border)] px-2.5 py-2.5">
          <div className="shrink-0 flex items-center justify-center">
            <FileIcon
              size={16}
              className={
                isProject
                  ? "text-violet-500 dark:text-violet-400"
                  : fileInfo.color
              }
            />
          </div>
          <div className="flex-1 min-w-0">
            <p
              className="truncate text-[13px] leading-tight text-[var(--theme-text)]"
              title={file.file_name}
            >
              {file.file_name}
            </p>
          </div>
          <button
            onClick={(e) => {
              e.stopPropagation();
              ctx.show(e, file);
            }}
            aria-label={t("common.more", "More")}
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-bg-sidebar)] hover:text-[var(--theme-text)]"
          >
            <MoreHorizontal
              size={15}
              className="text-current"
            />
          </button>
        </div>

        {/* Preview area */}
        <div className="aspect-[16/9] overflow-hidden relative bg-[var(--theme-bg-sidebar)]">
          <FileCardPreview preview={cardPreview} icon={FileIcon} />
        </div>

        {/* Meta footer */}
        <div className="px-2.5 py-2">
          <p className="truncate text-[11px] text-[var(--theme-text-secondary)]">
            {meta}
          </p>
        </div>
      </div>

      <FileContextMenu
        menu={ctx.menu}
        menuRef={ctx.menuRef}
        file={file}
        onGoToSession={onGoToSession}
        onToggleFavorite={onToggleFavorite}
      />
    </>
  );
}
