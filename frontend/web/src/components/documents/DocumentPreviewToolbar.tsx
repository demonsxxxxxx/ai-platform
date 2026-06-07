import { BackIcon } from "../common/BackIcon";
import { FileIcon } from "../common/FileIcon";
import {
  X,
  Copy,
  Check,
  Download,
  Expand,
  Shrink,
  Eye,
  Code2,
  PanelRight,
  Columns2,
} from "lucide-react";
import { formatFileSize as formatFileSizeUtil } from "./utils";
import type { DocumentPreviewState } from "./useDocumentPreviewState";

type ToolbarProps = Pick<
  DocumentPreviewState,
  | "t"
  | "data"
  | "copied"
  | "viewSource"
  | "isSidebar"
  | "isFullscreen"
  | "toolbarCompact"
  | "markdownFile"
  | "codeFile"
  | "hasTextContent"
  | "displaySize"
  | "fileSize"
  | "fileName"
  | "language"
  | "fileInfo"
  | "Icon"
  | "s3Key"
  | "signedUrl"
  | "externalImageUrl"
  | "resolvedUrl"
  | "unsupportedPreviewFile"
  | "onUserInteraction"
  | "onClose"
  | "effectiveOnBack"
  | "handleCopy"
  | "handleDownload"
  | "toolbarRef"
  | "setViewSource"
  | "setViewMode"
  | "setIsFullscreen"
>;

export default function DocumentPreviewToolbar({
  t,
  data,
  copied,
  viewSource,
  isSidebar,
  isFullscreen,
  toolbarCompact,
  markdownFile,
  codeFile,
  hasTextContent,
  displaySize,
  fileSize,
  fileName,
  language,
  fileInfo,
  Icon,
  s3Key,
  signedUrl,
  externalImageUrl,
  resolvedUrl,
  unsupportedPreviewFile,
  onUserInteraction,
  onClose,
  effectiveOnBack,
  handleCopy,
  handleDownload,
  toolbarRef,
  setViewSource,
  setViewMode,
  setIsFullscreen,
}: ToolbarProps) {
  return (
    <div className="flex items-center gap-1.5 sm:gap-2.5 px-2 sm:px-4 py-2 sm:py-3 border-b border-[var(--theme-border)] overflow-hidden">
      {effectiveOnBack && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            effectiveOnBack();
          }}
          className="flex items-center justify-center size-8 sm:size-9 rounded-lg sm:rounded-xl hover:bg-stone-200/80 dark:hover:bg-stone-700/60 active:bg-stone-200 dark:active:bg-stone-600/60 transition-all duration-200 active:scale-95 cursor-pointer shrink-0"
          title={t("common.back", "Back")}
        >
          <BackIcon size={16} className="text-stone-500 dark:text-stone-400" />
        </button>
      )}
      <FileIcon icon={Icon} bg={fileInfo.bg} color={fileInfo.color} />
      <div className="flex-1 min-w-0 overflow-hidden">
        <h3
          className="text-[13px] sm:text-sm font-medium text-[var(--theme-text)] truncate"
          title={fileName}
        >
          {fileName}
        </h3>
        <div className="flex items-center gap-1 sm:gap-1.5 text-xs text-[var(--theme-text-secondary)] mt-0.5">
          {codeFile && (
            <span className="px-1 py-0 sm:px-1.5 sm:py-0.5 rounded bg-[var(--theme-primary-light)] font-mono text-[10px] sm:text-xs shrink-0">
              {language}
            </span>
          )}
          <span className="text-[10px] sm:text-xs truncate">
            {hasTextContent
              ? t("documents.chars", { count: displaySize })
              : fileSize
                ? formatFileSizeUtil(fileSize)
                : fileInfo.label}
          </span>
        </div>
      </div>
      <div
        ref={toolbarRef}
        className="flex items-center gap-px sm:gap-1 relative z-10 shrink-0"
      >
        {markdownFile && data?.content && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setViewSource(!viewSource);
            }}
            className="flex items-center justify-center size-8 sm:size-auto sm:gap-1.5 sm:px-2.5 sm:py-2 rounded-lg sm:rounded-xl text-stone-600 dark:text-stone-300 hover:bg-stone-200/80 dark:hover:bg-stone-700/60 active:bg-stone-200 dark:active:bg-stone-600/60 transition-all duration-200 active:scale-95 cursor-pointer"
            title={viewSource ? t("documents.preview") : t("documents.source")}
          >
            {viewSource ? (
              <>
                <Eye size={16} />
                {!toolbarCompact && (
                  <span className="hidden sm:inline">
                    {t("documents.preview")}
                  </span>
                )}
              </>
            ) : (
              <>
                <Code2 size={16} />
                {!toolbarCompact && (
                  <span className="hidden sm:inline">
                    {t("documents.source")}
                  </span>
                )}
              </>
            )}
          </button>
        )}
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onUserInteraction?.();
            if (isSidebar) {
              setViewMode("center");
            } else {
              setViewMode("sidebar");
              if (isFullscreen) setIsFullscreen(false);
            }
          }}
          className="hidden sm:flex items-center justify-center gap-1.5 px-2.5 py-2 rounded-xl text-xs sm:text-sm font-medium text-stone-600 dark:text-stone-300 hover:bg-stone-200/80 dark:hover:bg-stone-700/60 active:bg-stone-200 dark:active:bg-stone-600/60 transition-all duration-200 active:scale-95 cursor-pointer"
          title={
            isSidebar
              ? t("documents.centerView", "Center view")
              : t("documents.sidebarView", "Sidebar view")
          }
        >
          {isSidebar ? (
            <>
              <Columns2 size={16} />
              {!toolbarCompact && (
                <span>{t("documents.centerView", "居中")}</span>
              )}
            </>
          ) : (
            <>
              <PanelRight size={16} />
              {!toolbarCompact && <span>{t("documents.sidebarView")}</span>}
            </>
          )}
        </button>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onUserInteraction?.();
            if (!isFullscreen && isSidebar) {
              setViewMode("center");
            }
            setIsFullscreen(!isFullscreen);
          }}
          className="flex items-center justify-center size-8 sm:size-auto sm:gap-1.5 sm:px-2.5 sm:py-2 rounded-lg sm:rounded-xl text-stone-600 dark:text-stone-300 hover:bg-stone-200/80 dark:hover:bg-stone-700/60 active:bg-stone-200 dark:active:bg-stone-600/60 transition-all duration-200 active:scale-95 cursor-pointer"
          title={
            isFullscreen
              ? t("documents.exitFullscreen")
              : t("documents.fullscreen")
          }
        >
          {isFullscreen ? (
            <>
              <Shrink size={16} />
              {!toolbarCompact && (
                <span className="hidden sm:inline">
                  {t("documents.exitFullscreen")}
                </span>
              )}
            </>
          ) : (
            <>
              <Expand size={16} />
              {!toolbarCompact && (
                <span className="hidden sm:inline">
                  {t("documents.fullscreen")}
                </span>
              )}
            </>
          )}
        </button>
        {(data?.content ||
          s3Key ||
          signedUrl ||
          externalImageUrl ||
          resolvedUrl) && (
          <>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                handleDownload();
              }}
              className="flex items-center justify-center size-8 sm:size-auto sm:gap-1.5 sm:px-2.5 sm:py-2 rounded-lg sm:rounded-xl text-stone-600 dark:text-stone-300 hover:bg-stone-200/80 dark:hover:bg-stone-700/60 active:bg-stone-200 dark:active:bg-stone-600/60 transition-all duration-200 active:scale-95 cursor-pointer"
              title={t("documents.download")}
            >
              <Download size={16} />
              {!toolbarCompact && (
                <span className="hidden sm:inline">
                  {t("documents.download")}
                </span>
              )}
            </button>
            {data?.content && !unsupportedPreviewFile && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  handleCopy();
                }}
                className="flex items-center justify-center size-8 sm:size-auto sm:gap-1.5 sm:px-2.5 sm:py-2 rounded-lg sm:rounded-xl text-stone-600 dark:text-stone-300 hover:bg-stone-200/80 dark:hover:bg-stone-700/60 active:bg-stone-200 dark:active:bg-stone-600/60 transition-all duration-200 active:scale-95 cursor-pointer"
              >
                {copied ? (
                  <>
                    <Check
                      size={16}
                      className="text-green-500 dark:text-green-400"
                    />
                    {!toolbarCompact && (
                      <span className="hidden sm:inline text-green-500 dark:text-green-400">
                        {t("documents.copied")}
                      </span>
                    )}
                  </>
                ) : (
                  <>
                    <Copy size={16} />
                    {!toolbarCompact && (
                      <span className="hidden sm:inline">
                        {t("documents.copy")}
                      </span>
                    )}
                  </>
                )}
              </button>
            )}
          </>
        )}
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onClose();
          }}
          className="flex items-center justify-center size-8 sm:size-9 rounded-lg sm:rounded-xl hover:bg-stone-200/80 dark:hover:bg-stone-700/60 active:bg-stone-200 dark:active:bg-stone-600/60 transition-all duration-200 active:scale-95 cursor-pointer"
          aria-label={t("common.close")}
        >
          <X size={16} className="text-stone-500 dark:text-stone-400" />
        </button>
      </div>
    </div>
  );
}
