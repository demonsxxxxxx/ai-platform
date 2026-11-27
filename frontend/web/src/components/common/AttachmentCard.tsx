import { Button } from "@lobehub/ui/base-ui";
import { memo } from "react";
import { X, Loader2, Clock3 } from "lucide-react";
import { useTranslation } from "react-i18next";
import clsx from "clsx";
import type { MessageAttachment } from "../../types";
import {
  getFileTypeInfo,
  formatFileSize as formatFileSizeUtil,
} from "../documents/utils";
import { useSafeAttachmentImageSrc } from "./attachmentImageSafety";
import { AttachmentFilename } from "./AttachmentFilename";
import { AttachmentFilenameTooltip } from "./AttachmentFilenameTooltip";

// Re-export formatFileSize for external use
// eslint-disable-next-line react-refresh/only-export-components
export const formatFileSize = formatFileSizeUtil;

// Re-export for backward compatibility
// eslint-disable-next-line react-refresh/only-export-components
export function getAttachmentIconInfo(
  mimeType: string,
  fileName?: string,
): {
  icon: React.ElementType;
  bgColor: string;
  iconColor: string;
  label: string;
} {
  const info = getFileTypeInfo(fileName || "", mimeType);
  return {
    icon: info.icon,
    bgColor: info.bg,
    iconColor: info.color,
    label: info.label,
  };
}

export interface AttachmentCardProps {
  attachment: MessageAttachment;
  /** Click the tile to preview the authorized attachment URL. */
  onClick?: (previewSrc?: string) => void;
  /** Remove a completed attachment. */
  onRemove?: () => void;
  /** Cancel an in-flight upload. */
  onCancel?: () => void;
  /** Composer attachments are editable; message attachments are read-only. */
  variant?: "editable" | "preview";
  /** Compact is used in the composer; default is used in messages. */
  size?: "default" | "compact";
  /** Whether upload is in progress. */
  isUploading?: boolean;
}

export const AttachmentCard = memo(function AttachmentCard({
  attachment,
  onClick,
  onRemove,
  onCancel,
  variant = "preview",
  size = "default",
  isUploading = false,
}: AttachmentCardProps) {
  const { t } = useTranslation();
  const {
    icon: FileIcon,
    bgColor,
    iconColor,
    label,
  } = getAttachmentIconInfo(attachment.mimeType, attachment.name);
  const imageSrc =
    useSafeAttachmentImageSrc(attachment.url, attachment.mimeType) ?? undefined;
  const isImage = Boolean(imageSrc);
  const isCompact = size === "compact";
  const isQueued = attachment.uploadStatus === "queued";
  const isRetrying = attachment.uploadStatus === "retrying";
  const isConfirming = isUploading && !isQueued && !isRetrying && (attachment.uploadProgress ?? 0) >= 100;
  const removeButtonClass =
    "composer-attachment-remove flex shrink-0 items-center justify-center rounded-full text-[var(--theme-text-secondary)]";

  const handleClick = () => {
    onClick?.(imageSrc);
  };

  const handleRemove = (event: React.MouseEvent<HTMLElement>) => {
    event.stopPropagation();
    onRemove?.();
  };

  const previewLabel = isImage
    ? `${t("imageViewer.fullscreen")} ${attachment.name}`
    : `${t("documents.preview")} ${attachment.name}`;
  const state = isUploading
    ? isQueued
      ? "queued"
      : isRetrying
        ? "retrying"
        : isConfirming ? "confirming" : "uploading"
    : "complete";

  // This is the props-driven equivalent of assistant-ui's AttachmentPrimitive.Root.
  if (isCompact) {
    const tile = (
        <Button
          type="text"
          htmlType="button"
          data-slot="attachment-tile"
          onClick={handleClick}
          disabled={isUploading || !onClick}
          aria-label={isUploading ? attachment.name : previewLabel}
          style={{
            height: "auto",
            minWidth: 0,
            flex: "1 1 0%",
            padding: "6px 5px 6px 7px",
            background: "transparent",
            border: "none",
            boxShadow: "none",
            justifyContent: "flex-start",
            textAlign: "left",
          }}
          className="composer-attachment-tile"
        >
          <div
            className={clsx(
              "shrink-0 flex items-center justify-center rounded-lg overflow-hidden",
              "transition-transform duration-200",
              !isUploading && "group-hover:scale-105",
              isImage ? "size-10" : "size-10 bg-[var(--theme-bg-card)]",
            )}
          >
            {isUploading ? (
              isQueued ? (
                <Clock3 size={18} className="text-[var(--theme-text-secondary)]" aria-hidden="true" />
              ) : (
                <Loader2
                  size={18}
                  className={"text-[var(--theme-text-secondary)] animate-spin"}
                  aria-hidden="true"
                />
              )
            ) : isImage ? (
              <img
                src={imageSrc}
                alt={attachment.name}
                className="h-full w-full object-cover"
              />
            ) : (
              <FileIcon size={18} className="text-[var(--theme-text-secondary)]" aria-hidden="true" />
            )}
          </div>

          <div className="flex min-w-0 flex-1 flex-col">
            <AttachmentFilename name={attachment.name} className="text-[13px] font-medium leading-5 text-[var(--theme-text)]" />
            <span className="mt-0.5 truncate text-xs text-[var(--theme-text-secondary)]">
              {isUploading
                ? isQueued
                  ? t("fileUpload.waiting")
                  : isRetrying
                    ? t("fileUpload.waitingForCapacity")
                    : isConfirming
                      ? t("fileUpload.confirming")
                      : `${attachment.uploadProgress ?? 0}%`
                : formatFileSize(attachment.size)}
            </span>
            {isUploading && !isQueued && !isRetrying && !isConfirming && (
              <div
                className="composer-attachment-progress overflow-hidden bg-[var(--theme-border)]"
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={attachment.uploadProgress ?? 0}
                aria-label={`${attachment.name} ${attachment.uploadProgress ?? 0}%`}
              >
                <div
                  className="h-full rounded-full bg-[var(--theme-primary)] transition-[width] duration-150"
                  style={{ width: `${attachment.uploadProgress ?? 0}%` }}
                />
              </div>
            )}
          </div>
        </Button>
    );

    return (
      <div
        data-slot="attachment-root"
        data-state={state}
        data-attachment-source="composer"
        className="composer-attachment-card"
      >
        {isUploading || !onClick ? tile : (
          <AttachmentFilenameTooltip name={attachment.name}>
            {tile}
          </AttachmentFilenameTooltip>
        )}
        {isConfirming && <span role="status" className="sr-only">{attachment.name}：{t("fileUpload.confirming")}</span>}
        {variant === "editable" &&
          (isUploading && onCancel ? (
            <Button
              type="text"
              htmlType="button"
              onClick={(event) => {
                event.stopPropagation();
                onCancel();
              }}
              style={{ height: 24, width: 24, minWidth: 24, padding: 0 }}
              className={removeButtonClass}
              title={t("fileUpload.cancelUpload")}
              aria-label={`${t("fileUpload.cancelUpload")} ${attachment.name}`}
            >
              <X size={12} aria-hidden="true" />
            </Button>
          ) : (
            !isUploading && onRemove && (
              <Button
                type="text"
                htmlType="button"
                onClick={handleRemove}
                style={{ height: 24, width: 24, minWidth: 24, padding: 0 }}
                className={removeButtonClass}
                aria-label={`${t("fileUpload.removeAttachment")} ${attachment.name}`}
              >
                <X size={12} aria-hidden="true" />
              </Button>
            )
          ))}
      </div>
    );
  }

  return (
    <Button
      type="text"
      htmlType="button"
      data-slot="attachment-root"
      data-state={state}
      data-attachment-source="message"
      onClick={handleClick}
      aria-label={previewLabel}
      style={{
        height: "auto",
        minHeight: 48,
        width: "100%",
        padding: 0,
        justifyContent: "flex-start",
        textAlign: "left",
      }}
      className={clsx(
        "group relative flex items-center overflow-hidden",
        "h-12 sm:h-14 min-w-[200px] max-w-[280px] sm:min-w-[240px] sm:max-w-[320px]",
        "rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] dark:bg-neutral-900",
        "shadow-[0_4px_12px_rgba(18,38,63,0.03)]",
        "text-left cursor-pointer select-none",
        "transition-colors duration-150 ease-out",
        "hover:bg-[var(--theme-bg-sidebar)] active:opacity-90",
        isUploading && "pointer-events-none",
      )}
    >
      <div
        className={clsx(
          "shrink-0 flex items-center justify-center",
          "transition-transform duration-300",
          !isUploading && "group-hover:scale-105",
          isImage
            ? "size-12 overflow-hidden rounded-l-lg sm:size-14"
            : clsx("size-12 rounded-l-lg sm:size-14", bgColor),
        )}
      >
        {isUploading ? (
          isQueued ? (
            <Clock3 size={18} className={iconColor} aria-hidden="true" />
          ) : (
            <Loader2
              size={18}
              className={clsx(iconColor, "animate-spin")}
              aria-hidden="true"
            />
          )
        ) : isImage ? (
          <img
            src={imageSrc}
            alt={attachment.name}
            className="h-full w-full object-cover"
          />
        ) : (
          <FileIcon
            size={18}
            className={clsx(
              iconColor,
              "transition-transform duration-300 group-hover:scale-110",
            )}
            aria-hidden="true"
          />
        )}
      </div>

      <div className="flex min-w-0 flex-1 flex-col justify-center px-3 py-2 sm:px-3.5">
        <div className="truncate text-[13px] font-medium leading-tight text-[var(--theme-text)] sm:text-sm">
          {attachment.name}
        </div>
        <div className="mt-0.5 flex items-center justify-between text-[11px] text-[var(--theme-text-secondary)] sm:mt-1 sm:text-xs">
          <span className="truncate capitalize">{label}</span>
          <span className="ml-2 shrink-0">
            {isUploading
              ? isQueued
                ? t("fileUpload.waiting")
                : isRetrying
                  ? t("fileUpload.waitingForCapacity")
                  : t("fileUpload.uploading")
              : formatFileSize(attachment.size)}
          </span>
        </div>
      </div>
    </Button>
  );
});
