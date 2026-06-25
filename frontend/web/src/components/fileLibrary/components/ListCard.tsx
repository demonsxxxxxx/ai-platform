import { useTranslation } from "react-i18next";
import { MoreHorizontal } from "lucide-react";
import type { RevealedFileItem } from "../../../services/api";
import { getFileTypeInfo } from "../../documents/utils";
import { useContextMenu } from "../hooks/useContextMenu";
import { buildFileCardPreview, buildMeta } from "../utils";
import { FileContextMenu } from "./FileContextMenu";
import { FileCardPreview } from "./FileCardPreview";

interface ListCardProps {
  file: RevealedFileItem;
  onPreview: (file: RevealedFileItem) => void;
  onGoToSession: (sessionId: string, file?: RevealedFileItem) => void;
  onToggleFavorite: (file: RevealedFileItem) => void;
}

export function ListCard({
  file,
  onPreview,
  onGoToSession,
  onToggleFavorite,
}: ListCardProps) {
  const { t } = useTranslation();
  const fileInfo = getFileTypeInfo(file.file_name, file.mime_type || undefined);
  const FileIcon = fileInfo.icon;
  const cardPreview = buildFileCardPreview(file);
  const meta = buildMeta(file, t);
  const ctx = useContextMenu();

  return (
    <>
      <div
        onClick={() => onPreview(file)}
        onContextMenu={(e) => ctx.show(e, file)}
        className="group/card relative flex cursor-pointer select-none items-center gap-3.5 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-4 py-3 shadow-[0_1px_2px_rgba(18,38,63,0.04)] transition-all duration-150 hover:border-[var(--theme-border-strong)] hover:shadow-[0_8px_18px_rgba(18,38,63,0.05)]"
      >
        {/* Icon / thumbnail */}
        <div className="shrink-0">
          <div className="h-10 w-10 overflow-hidden rounded-lg ring-1 ring-[var(--theme-border)]">
            <FileCardPreview preview={cardPreview} icon={FileIcon} compact />
          </div>
        </div>

        {/* Name + meta */}
        <div className="flex-1 min-w-0">
          <p className="truncate text-[13px] font-medium leading-snug text-[var(--theme-text)]">
            {file.file_name}
          </p>
          <p className="mt-0.5 truncate text-[11px] text-[var(--theme-text-secondary)]">
            {meta}
          </p>
        </div>

        {/* More button */}
        <button
          onClick={(e) => {
            e.stopPropagation();
            ctx.show(e, file);
          }}
          aria-label={t("common.more", "More")}
          className="shrink-0 rounded-md p-1.5 text-[var(--theme-text-secondary)] transition-all hover:bg-[var(--theme-bg-sidebar)] hover:text-[var(--theme-text)]"
        >
          <MoreHorizontal size={16} />
        </button>
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
