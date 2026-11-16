import { Button } from "@lobehub/ui/base-ui";
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
        <Button type="text"
          htmlType="button"
          icon={<BackIcon size={16} />}
          aria-label={t("common.back", "Back")}
          onClick={(e) => {
            e.stopPropagation();
            effectiveOnBack();
          }}
          className="shrink-0"
        />
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
          <Button type="text"
            htmlType="button"
            icon={viewSource ? <Eye size={16} /> : <Code2 size={16} />}
            aria-label={viewSource ? t("documents.preview") : t("documents.source")}
            onClick={(e) => {
              e.stopPropagation();
              setViewSource(!viewSource);
            }}
          >
            {!toolbarCompact && (viewSource ? t("documents.preview") : t("documents.source"))}
          </Button>
        )}
        <span className="hidden sm:contents">
          <Button type="text"
            htmlType="button"
            icon={isSidebar ? <Columns2 size={16} /> : <PanelRight size={16} />}
            aria-label={isSidebar ? t("documents.centerView", "Center view") : t("documents.sidebarView", "Sidebar view")}
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
          >
            {!toolbarCompact && (isSidebar ? t("documents.centerView", "居中") : t("documents.sidebarView"))}
          </Button>
        </span>
        <Button type="text"
          htmlType="button"
          icon={isFullscreen ? <Shrink size={16} /> : <Expand size={16} />}
          aria-label={isFullscreen ? t("documents.exitFullscreen") : t("documents.fullscreen")}
          onClick={(e) => {
            e.stopPropagation();
            onUserInteraction?.();
            if (!isFullscreen && isSidebar) setViewMode("center");
            setIsFullscreen(!isFullscreen);
          }}
        >
          {!toolbarCompact && (isFullscreen ? t("documents.exitFullscreen") : t("documents.fullscreen"))}
        </Button>
        {(data?.content || s3Key || signedUrl || externalImageUrl || resolvedUrl) && (
          <>
            <Button type="text"
              htmlType="button"
              icon={<Download size={16} />}
              aria-label={t("documents.download")}
              onClick={(e) => {
                e.stopPropagation();
                handleDownload();
              }}
            >
              {!toolbarCompact && t("documents.download")}
            </Button>
            {data?.content && !unsupportedPreviewFile && (
              <Button type="text"
                htmlType="button"
                icon={copied ? <Check size={16} className="text-[var(--theme-success-text)]" /> : <Copy size={16} />}
                aria-label={copied ? t("documents.copied") : t("documents.copy")}
                onClick={(e) => {
                  e.stopPropagation();
                  handleCopy();
                }}
              >
                {!toolbarCompact && (copied ? t("documents.copied") : t("documents.copy"))}
              </Button>
            )}
          </>
        )}
        <Button type="text"
          htmlType="button"
          icon={<X size={16} />}
          onClick={(e) => {
            e.stopPropagation();
            onClose();
          }}
          aria-label={t("common.close")}
        />
      </div>
    </div>
  );
}
