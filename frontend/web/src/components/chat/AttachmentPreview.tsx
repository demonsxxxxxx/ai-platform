import { memo } from "react";
import { X, FileText, Image, Video, Music, File } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { MessageAttachment } from "../../types";

interface AttachmentPreviewProps {
  attachments: MessageAttachment[];
  onRemove: (id: string) => void;
  onCancel?: (id: string) => void;
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const ICON_MAP = {
  image: Image,
  video: Video,
  audio: Music,
  document: FileText,
};

export const AttachmentPreview = memo(function AttachmentPreview({
  attachments,
  onRemove,
  onCancel,
}: AttachmentPreviewProps) {
  const { t } = useTranslation();

  return (
    <div className="space-y-2 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] p-2 shadow-[0_4px_12px_rgba(18,38,63,0.03)] dark:bg-stone-900">
      {attachments.map((attachment) => {
        const Icon = ICON_MAP[attachment.type] || File;
        const isUploading = !!attachment.isUploading;

        return (
          <div
            key={attachment.id}
            className="relative flex items-center gap-2 overflow-hidden rounded-md bg-[var(--theme-bg-sidebar)] p-2"
          >
            {/* Progress bar */}
            {isUploading && (
              <div
                className="absolute inset-y-0 left-0 bg-blue-400/20 dark:bg-blue-500/20 transition-all duration-200"
                style={{
                  width: `${attachment.uploadProgress ?? 0}%`,
                }}
              />
            )}

            {/* Preview or icon */}
            {attachment.type === "image" &&
            attachment.mimeType.startsWith("image/") ? (
              <div className="relative z-[1] h-10 w-10 flex-shrink-0 overflow-hidden rounded bg-[var(--theme-bg-card)] ring-1 ring-[var(--theme-border)]">
                <img
                  src={attachment.url}
                  alt={attachment.name}
                  className="w-full h-full object-cover"
                />
              </div>
            ) : (
              <div className="relative z-[1] flex h-10 w-10 flex-shrink-0 items-center justify-center rounded bg-[var(--theme-bg-card)] ring-1 ring-[var(--theme-border)]">
                <Icon
                  size={18}
                  className="text-[var(--theme-text-secondary)]"
                />
              </div>
            )}

            {/* File info */}
            <div className="flex-1 min-w-0 relative z-[1]">
              <p className="truncate text-sm font-medium text-[var(--theme-text)]">
                {attachment.name}
              </p>
              <p className="text-xs text-[var(--theme-text-secondary)]">
                {isUploading
                  ? `${attachment.uploadProgress ?? 0}%`
                  : formatFileSize(attachment.size)}
              </p>
            </div>

            {/* Remove / Cancel button */}
            <button
              type="button"
              onClick={() =>
                isUploading && onCancel
                  ? onCancel(attachment.id)
                  : onRemove(attachment.id)
              }
              className="relative z-[1] rounded-lg p-1 text-[var(--theme-text-secondary)] hover:bg-[var(--theme-bg-card)] hover:text-[var(--theme-text)]"
              title={
                isUploading
                  ? t("fileUpload.cancelUpload")
                  : t("fileUpload.removeAttachment")
              }
            >
              <X size={14} />
            </button>
          </div>
        );
      })}
    </div>
  );
});
