import { useTranslation } from "react-i18next";
import { MessageSquare, Star, Download, ExternalLink } from "lucide-react";
import type { RevealedFileItem } from "../../../services/api";
import {
  downloadPreviewUrl,
  openPreviewUrl,
} from "../../documents/documentPreviewSources";
import { resolveSafeRevealedFilePreviewUrl } from "../utils";

interface FileContextMenuProps {
  menu: { x: number; y: number; file: RevealedFileItem } | null;
  menuRef: React.RefObject<HTMLDivElement | null>;
  file: RevealedFileItem;
  onGoToSession: (sessionId: string, file?: RevealedFileItem) => void;
  onToggleFavorite: (file: RevealedFileItem) => void;
}

export function FileContextMenu({
  menu,
  menuRef,
  file,
  onGoToSession,
  onToggleFavorite,
}: FileContextMenuProps) {
  const { t } = useTranslation();
  if (!menu) return null;

  const isProject = file.file_type === "project";
  const safePreviewUrl = isProject
    ? null
    : resolveSafeRevealedFilePreviewUrl(file.preview_url);
  const safeDownloadUrl = isProject
    ? null
    : resolveSafeRevealedFilePreviewUrl(file.download_url);
  const hasPreviewUrl = !!safePreviewUrl;
  const hasDownloadUrl = !!safeDownloadUrl;

  const items: {
    icon: typeof MessageSquare;
    label: string;
    action: () => void;
  }[] = [
    {
      icon: MessageSquare,
      label: t("fileLibrary.context.goToSession"),
      action: () => onGoToSession(file.session_id, file),
    },
    {
      icon: Star,
      label: file.is_favorite
        ? t("fileLibrary.context.unfavorite")
        : t("fileLibrary.context.favorite"),
      action: () => onToggleFavorite(file),
    },
    ...(hasDownloadUrl
      ? [
          {
            icon: Download,
            label: t("fileLibrary.context.download"),
            action: () => {
              void downloadPreviewUrl({
                url: safeDownloadUrl,
                fileName: file.file_name || "download",
              });
            },
          },
        ]
      : []),
    ...(hasPreviewUrl
      ? [
          {
            icon: ExternalLink,
            label: t("fileLibrary.context.openInNewTab"),
            action: () => {
              void openPreviewUrl({
                url: safePreviewUrl,
                fileName: file.file_name,
                mimeType: file.mime_type,
              });
            },
          },
        ]
      : []),
  ];

  return (
    <div
      ref={menuRef}
      className="fixed z-[999] min-w-[240px] rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] p-1 shadow-[0_8px_24px_rgba(18,38,63,0.10)] dark:shadow-black/30"
      style={{ position: "fixed", top: menu.y, left: menu.x }}
      onClick={(e) => e.stopPropagation()}
    >
      {items.map((item) => (
        <button
          key={item.label}
          onClick={item.action}
          className="flex w-full cursor-pointer items-center gap-2 rounded-lg p-2 text-[13px] text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-bg-sidebar)] hover:text-[var(--theme-text)]"
        >
          <div className="size-5 flex items-center justify-center shrink-0">
            <item.icon
              size={16}
              className="text-[var(--theme-text-secondary)]"
            />
          </div>
          <span className="flex-1 text-left">{item.label}</span>
        </button>
      ))}
    </div>
  );
}
