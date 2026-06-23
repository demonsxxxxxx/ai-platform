import { memo } from "react";
import { X, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import clsx from "clsx";
import type { MessageAttachment } from "../../types";
import {
  getFileTypeInfo,
  formatFileSize as formatFileSizeUtil,
} from "../documents/utils";
import { useSafeAttachmentImageSrc } from "./attachmentImageSafety";

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
  /** 点击卡片时的回调（预览） */
  onClick?: (previewSrc?: string) => void;
  /** 删除按钮点击回调 */
  onRemove?: () => void;
  /** 取消上传按钮点击回调 */
  onCancel?: () => void;
  /** 显示模式：editable 显示删除按钮，preview 显示预览指示器 */
  variant?: "editable" | "preview";
  /** 尺寸：compact 更紧凑，适合输入框区域 */
  size?: "default" | "compact";
  /** Whether upload is in progress */
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
  const removeButtonClass =
    "shrink-0 flex size-6 items-center justify-center rounded-lg text-[var(--theme-text-secondary)] opacity-100 transition-colors duration-150 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-500/10 dark:hover:text-red-300";

  const handleClick = () => {
    onClick?.(imageSrc);
  };

  const handleRemove = (e: React.MouseEvent) => {
    e.stopPropagation();
    onRemove?.();
  };

  // 紧凑模式样式（用于 ChatInput）
  if (isCompact) {
    return (
      <div
        onClick={handleClick}
        className={clsx(
          "group relative flex items-center gap-2.5 px-3 py-2",
          "rounded-lg border border-[var(--theme-border)]",
          "bg-[var(--theme-bg-card)] dark:bg-stone-900",
          "shadow-[0_4px_12px_rgba(18,38,63,0.03)] cursor-pointer select-none",
          "transition-colors duration-150 ease-out",
          "hover:bg-[var(--theme-bg-sidebar)]",
          "active:opacity-90",
          isUploading && !onCancel && "pointer-events-none",
        )}
      >
        {/* 图标/图片 */}
        <div
          className={clsx(
            "shrink-0 flex items-center justify-center rounded-lg overflow-hidden",
            "transition-transform duration-200",
            !isUploading && "group-hover:scale-105",
            isImage ? "size-10" : clsx("size-10", bgColor),
          )}
        >
          {isUploading ? (
            <Loader2 size={18} className={clsx(iconColor, "animate-spin")} />
          ) : isImage ? (
            <img
              src={imageSrc}
              alt={attachment.name}
              className="w-full h-full object-cover"
            />
          ) : (
            <FileIcon size={18} className={iconColor} />
          )}
        </div>

        {/* 文件信息 */}
        <div className="flex flex-col min-w-0 flex-1">
          <span className="max-w-[120px] truncate text-[13px] font-medium leading-tight text-[var(--theme-text)] sm:max-w-[160px]">
            {attachment.name}
          </span>
          <span className="mt-0.5 text-xs text-[var(--theme-text-secondary)]">
            {isUploading
              ? `${attachment.uploadProgress ?? 0}%`
              : formatFileSize(attachment.size)}
          </span>
        </div>

        {/* 删除/取消按钮 */}
        {variant === "editable" &&
          (isUploading && onCancel ? (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onCancel();
              }}
              className={removeButtonClass}
              title={t("fileUpload.cancelUpload")}
            >
              <X size={12} />
            </button>
          ) : (
            onRemove && (
              <button
                type="button"
                onClick={handleRemove}
                className={clsx(
                  removeButtonClass,
                  "sm:opacity-0 sm:group-hover:opacity-100",
                )}
              >
                <X size={12} />
              </button>
            )
          ))}
      </div>
    );
  }

  // 默认模式样式（用于 ChatMessage）
  return (
    <button
      onClick={handleClick}
      className={clsx(
        "group relative flex items-center overflow-hidden",
        "h-12 sm:h-14 min-w-[200px] max-w-[280px] sm:min-w-[240px] sm:max-w-[320px]",
        "rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] dark:bg-stone-900",
        "shadow-[0_4px_12px_rgba(18,38,63,0.03)]",
        "text-left cursor-pointer select-none",
        "transition-colors duration-150 ease-out",
        "hover:bg-[var(--theme-bg-sidebar)]",
        "active:opacity-90",
        isUploading && "pointer-events-none",
      )}
      type="button"
    >
      {/* 左侧图标/图片区域 */}
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
          <Loader2 size={18} className={clsx(iconColor, "animate-spin")} />
        ) : isImage ? (
          <>
            <img
              src={imageSrc}
              alt={attachment.name}
              className="w-full h-full object-cover"
            />
            <div className="absolute inset-0 bg-gradient-to-t from-black/20 to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-300" />
          </>
        ) : (
          <FileIcon
            size={18}
            className={clsx(
              iconColor,
              "transition-transform duration-300 group-hover:scale-110",
            )}
          />
        )}
      </div>

      {/* 文件信息 */}
      <div className="flex flex-col justify-center px-3 sm:px-3.5 py-2 min-w-0 flex-1">
        <div className="truncate text-[13px] font-medium leading-tight text-[var(--theme-text)] sm:text-sm">
          {attachment.name}
        </div>
        <div className="mt-0.5 flex items-center justify-between text-[11px] text-[var(--theme-text-secondary)] sm:mt-1 sm:text-xs">
          <span className="capitalize truncate">{label}</span>
          <span className="shrink-0 ml-2">
            {isUploading
              ? t("fileUpload.uploading")
              : formatFileSize(attachment.size)}
          </span>
        </div>
      </div>
    </button>
  );
});
