/* eslint-disable react-refresh/only-export-components */
import { useState, useRef, useCallback, memo, useEffect } from "react";
import { createPortal } from "react-dom";
import { Paperclip, Image, Video, Music, FileText } from "lucide-react";
import { useTranslation } from "react-i18next";
import toast from "react-hot-toast";
import { useAuth } from "../../hooks/useAuth";
import { useFileUpload } from "../../hooks/useFileUpload";
import type { MessageAttachment, FileCategory } from "../../types";
import { Permission } from "../../types";

interface FileUploadButtonProps {
  attachments?: MessageAttachment[];
  onAttachmentsChange?: (
    attachments:
      | MessageAttachment[]
      | ((prev: MessageAttachment[]) => MessageAttachment[]),
  ) => void;
}

// Permission mapping
const CATEGORY_PERMISSIONS: Record<FileCategory, Permission> = {
  image: Permission.FILE_UPLOAD_IMAGE,
  video: Permission.FILE_UPLOAD_VIDEO,
  audio: Permission.FILE_UPLOAD_AUDIO,
  document: Permission.FILE_UPLOAD_DOCUMENT,
};

// Accept filters
const CATEGORY_ACCEPT_MAP: Record<FileCategory, string> = {
  image: "image/*",
  video: "video/*",
  audio: "audio/*",
  document:
    ".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.md,.csv,.rtf,.odt,.ods,.odp,.epub,.json,.dxf,.dwg,.log,.yaml,.yml,.toml,.ini,.cfg,.tex,.diff,.patch,.py,.js,.ts,.jsx,.tsx,.vue,.svelte,.go,.rs,.rb,.php,.java,.c,.cpp,.h,.cs,.swift,.kt,.scala,.dart,.lua,.r,.pl,.sql,.sh,.bash,.zsh,.fish,.ps1,.bat,.cmd,.properties,.gradle,.cmake,.env,.graphql,.proto,.zip,.rar,.7z,.tar,.gz,.bz2,.xz,.tgz",
};

const ACTIVE_UPLOAD_MIME_TYPES = new Set([
  "application/xhtml+xml",
  "application/xml",
  "image/svg+xml",
  "text/html",
  "text/xml",
]);

const ACTIVE_UPLOAD_EXTENSIONS = new Set([
  "htm",
  "html",
  "mhtml",
  "shtml",
  "svg",
  "xhtml",
  "xml",
]);

type UploadBatchValidationResult =
  | { ok: true }
  | {
      ok: false;
      reason:
        | "active_content_blocked"
        | "permission_denied"
        | "permission_probe_failed";
      blockedCategory: FileCategory;
      blockedFileName: string;
    };

function normalizeMimeType(value?: string | null): string {
  return value?.split(";", 1)[0]?.trim().toLowerCase() ?? "";
}

function getFileExtension(value?: string | null): string {
  if (!value) {
    return "";
  }
  const normalized = value.replace(/\\/g, "/");
  const segment = normalized.split("/").pop() ?? "";
  const lastDot = segment.lastIndexOf(".");
  return lastDot <= 0 ? "" : segment.slice(lastDot + 1).toLowerCase();
}

function isPotentiallyActiveUpload(file: Pick<File, "name" | "type">): boolean {
  if (ACTIVE_UPLOAD_MIME_TYPES.has(normalizeMimeType(file.type))) {
    return true;
  }
  return ACTIVE_UPLOAD_EXTENSIONS.has(getFileExtension(file.name));
}

/**
 * Resolve the effective upload category, preserving explicit user selection.
 */
export function inferUploadCategory(
  file: Pick<File, "type">,
  requestedCategory?: FileCategory,
): FileCategory {
  if (requestedCategory) {
    return requestedCategory;
  }
  if (file.type.startsWith("image/")) {
    return "image";
  }
  if (file.type.startsWith("video/")) {
    return "video";
  }
  if (file.type.startsWith("audio/")) {
    return "audio";
  }
  return "document";
}

/**
 * Validate a selected upload batch before any browser upload work begins.
 */
export function validateUploadBatch(
  files: readonly Pick<File, "name" | "type">[],
  options: {
    requestedCategory?: FileCategory;
    hasPermission: (permission: Permission) => boolean;
  },
): UploadBatchValidationResult {
  for (const file of files) {
    const fileCategory = inferUploadCategory(file, options.requestedCategory);
    if (isPotentiallyActiveUpload(file)) {
      return {
        ok: false,
        reason: "active_content_blocked",
        blockedCategory: fileCategory,
        blockedFileName: file.name,
      };
    }

    try {
      if (!options.hasPermission(CATEGORY_PERMISSIONS[fileCategory])) {
        return {
          ok: false,
          reason: "permission_denied",
          blockedCategory: fileCategory,
          blockedFileName: file.name,
        };
      }
    } catch {
      return {
        ok: false,
        reason: "permission_probe_failed",
        blockedCategory: fileCategory,
        blockedFileName: file.name,
      };
    }
  }

  return { ok: true };
}

// Icons
const CATEGORY_ICONS: Record<FileCategory, React.ElementType> = {
  image: Image,
  video: Video,
  audio: Music,
  document: FileText,
};

export const FileUploadButton = memo(function FileUploadButton({
  attachments = [],
  onAttachmentsChange,
}: FileUploadButtonProps) {
  const { t } = useTranslation();
  const { hasPermission } = useAuth();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const [showDropdown, setShowDropdown] = useState(false);
  const [selectedCategory, setSelectedCategory] = useState<FileCategory | null>(
    null,
  );

  const { uploadLimits, uploadFiles } = useFileUpload({
    attachments,
    onAttachmentsChange: onAttachmentsChange!,
  });

  // Get available categories based on permissions
  const availableCategories = Object.keys(CATEGORY_PERMISSIONS).filter((cat) =>
    (() => {
      try {
        return hasPermission(CATEGORY_PERMISSIONS[cat as FileCategory]);
      } catch {
        return false;
      }
    })(),
  ) as FileCategory[];

  // Check if user has any upload permission
  const canUpload = availableCategories.length > 0;

  // Close dropdown on outside click
  useEffect(() => {
    if (!showDropdown) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (triggerRef.current?.contains(e.target as Node)) return;
      if (dropdownRef.current?.contains(e.target as Node)) return;
      setShowDropdown(false);
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [showDropdown]);

  // Handle file selection from the dropdown or file picker
  const handleFiles = useCallback(
    (files: FileList | null, category?: FileCategory) => {
      if (!files || files.length === 0) return;

      const validation = validateUploadBatch(Array.from(files), {
        requestedCategory: category,
        hasPermission(permission) {
          return hasPermission(permission);
        },
      });
      if (!validation.ok) {
        if (validation.reason === "active_content_blocked") {
          toast.error(
            t("fileUpload.activeContentBlocked", {
              fileName: validation.blockedFileName,
              defaultValue:
                "Active HTML, SVG, or XML files are blocked for browser uploads.",
            }),
          );
          return;
        }

        if (validation.reason === "permission_probe_failed") {
          toast.error(
            t("fileUpload.permissionCheckFailed", {
              defaultValue:
                "Upload permissions could not be verified. Upload cancelled.",
            }),
          );
          return;
        }

        toast.error(
          t("fileUpload.noPermission", {
            type: t(`fileUpload.categories.${validation.blockedCategory}`),
          }),
        );
        return;
      }

      // Delegate count validation + upload to the hook
      uploadFiles(files, category);
    },
    [hasPermission, uploadFiles, t],
  );

  // Handle category selection from dropdown
  const handleCategorySelect = (category: FileCategory) => {
    setSelectedCategory(category);
    setShowDropdown(false);

    // Update file input accept filter and click
    if (fileInputRef.current) {
      fileInputRef.current.accept = CATEGORY_ACCEPT_MAP[category];
      fileInputRef.current.click();
    }
  };

  // Handle file input change
  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    handleFiles(e.target.files, selectedCategory || undefined);
    e.target.value = "";
  };

  const getDropdownStyle = (): React.CSSProperties => {
    const rect = triggerRef.current?.getBoundingClientRect();
    if (!rect) return { display: "none" };
    return {
      position: "fixed",
      bottom: window.innerHeight - rect.top + 8,
      left: rect.left,
      zIndex: 9999,
    };
  };

  if (!canUpload) return null;

  return (
    <>
      {/* Hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={handleFileChange}
      />

      {/* Upload button */}
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setShowDropdown(!showDropdown)}
        className="chat-tool-btn"
        title={t("fileUpload.title")}
      >
        <Paperclip size={18} />
      </button>

      {/* Dropdown menu via portal */}
      {showDropdown &&
        createPortal(
          <div
            ref={dropdownRef}
            className="w-52 rounded-xl shadow-lg border overflow-hidden animate-in fade-in slide-in-from-bottom-2 duration-200"
            style={{
              ...getDropdownStyle(),
              background: "var(--theme-bg-card)",
              borderColor: "var(--theme-border)",
            }}
          >
            {availableCategories.map((category) => {
              const Icon = CATEGORY_ICONS[category];
              return (
                <button
                  key={category}
                  type="button"
                  onClick={() => handleCategorySelect(category)}
                  className="w-full flex items-center gap-2.5 px-3.5 py-2.5 text-[13px] transition-colors hover:bg-[var(--theme-primary-light)] active:bg-[var(--theme-primary-light)]"
                  style={{ color: "var(--theme-text)" }}
                >
                  <div
                    className="flex items-center justify-center w-7 h-7 rounded-lg"
                    style={{ background: "var(--theme-primary-light)" }}
                  >
                    <Icon
                      size={14}
                      style={{ color: "var(--theme-text-secondary)" }}
                    />
                  </div>
                  <span className="flex-1 text-left font-medium">
                    {t(`fileUpload.categories.${category}`)}
                  </span>
                  {uploadLimits && (
                    <span
                      className="text-[11px] tabular-nums"
                      style={{ color: "var(--theme-text-secondary)" }}
                    >
                      {uploadLimits[category]}MB
                    </span>
                  )}
                </button>
              );
            })}
            {uploadLimits && (
              <div
                className="px-3.5 py-2 border-t text-xs"
                style={{
                  borderColor: "var(--theme-border)",
                  color: "var(--theme-text-secondary)",
                }}
              >
                {t("fileUpload.maxFilesSummary", {
                  maxFiles: uploadLimits.maxFiles,
                })}
              </div>
            )}
          </div>,
          document.body,
        )}
    </>
  );
});
