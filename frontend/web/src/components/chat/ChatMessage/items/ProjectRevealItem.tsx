import { useEffect, useMemo, useState } from "react";
import { Code2, FolderTree, Download } from "lucide-react";
import { useTranslation } from "react-i18next";
import { LoadingSpinner } from "../../../common";
import { exportProjectZip } from "../../../../utils/exportProjectZip";
import {
  getProjectRevealAutoOpenKey,
  markProjectRevealPreviewAutoOpened,
  shouldAutoOpenProjectRevealPreview,
} from "./projectRevealAutoOpen";
import {
  getCachedProjectRevealFiles,
  loadProjectRevealFilesCached,
  parseProjectRevealSummary,
  type RevealPreviewRequest,
} from "./revealPreviewData";
import {
  EMPTY_BINARY_FILES,
  areStringRecordMapsEqual,
  normalizeProjectRevealBinaryFiles,
  shouldReplaceProjectRevealFiles,
} from "./projectRevealState";
import type { RevealPreviewOpenSource } from "./revealPreviewState";

export function ProjectRevealItem({
  args,
  result,
  success,
  isPending,
  cancelled,
  allowAutoPreview,
  activePreview,
  onOpenPreview,
}: {
  args: Record<string, unknown>;
  result?: string | Record<string, unknown>;
  success?: boolean;
  isPending?: boolean;
  cancelled?: boolean;
  allowAutoPreview?: boolean;
  activePreview?: RevealPreviewRequest | null;
  onOpenPreview?: (
    preview: RevealPreviewRequest,
    source?: RevealPreviewOpenSource,
  ) => boolean;
}) {
  const { t } = useTranslation();
  const { projectName, mode, template, error, fileCount, projectPath, parsed } =
    useMemo(
      () =>
        parseProjectRevealSummary({
          args,
          result,
          parseErrorMessage: t("chat.message.toolParseError"),
        }),
      [args, result, t],
    );

  const projectAutoOpenKey = useMemo(
    () =>
      getProjectRevealAutoOpenKey({
        projectPath,
        projectName,
      }),
    [projectName, projectPath],
  );
  const isPreviewOpen =
    activePreview?.kind === "project" &&
    activePreview.previewKey === projectAutoOpenKey;
  const inlineFiles = parsed?.version === 1 ? parsed.files : null;
  const cachedProjectFiles = useMemo(
    () =>
      parsed?.version === 2
        ? getCachedProjectRevealFiles(projectAutoOpenKey)
        : null,
    [parsed, projectAutoOpenKey],
  );
  const [loadedFiles, setLoadedFiles] = useState<Record<string, string> | null>(
    cachedProjectFiles?.files || inlineFiles,
  );
  const [binaryFiles, setBinaryFiles] = useState<Record<string, string>>(
    normalizeProjectRevealBinaryFiles(cachedProjectFiles?.binaryFiles),
  );
  const previewRequest = useMemo(() => {
    if (!parsed || !projectAutoOpenKey) return null;

    return {
      kind: "project" as const,
      previewKey: projectAutoOpenKey,
      project: parsed,
    };
  }, [parsed, projectAutoOpenKey]);

  const openPreview = (
    openInFullscreen = false,
    source: RevealPreviewOpenSource = "manual",
  ) => {
    if (!previewRequest) return;
    onOpenPreview?.(
      {
        ...previewRequest,
        openInFullscreen,
      },
      source,
    );
  };

  useEffect(() => {
    const decision = shouldAutoOpenProjectRevealPreview({
      success,
      showFullPreview: isPreviewOpen,
      isDesktop: window.innerWidth >= 640,
      allowAutoPreview,
      previewKey: projectAutoOpenKey,
    });
    if (!decision || !previewRequest || !projectAutoOpenKey) {
      return;
    }

    const opened = onOpenPreview?.(previewRequest, "auto");

    if (opened) {
      markProjectRevealPreviewAutoOpened(projectAutoOpenKey);
    }
  }, [
    success,
    isPreviewOpen,
    allowAutoPreview,
    projectAutoOpenKey,
    previewRequest,
    onOpenPreview,
  ]);

  useEffect(() => {
    if (!parsed || parsed.version !== 2 || !projectAutoOpenKey || !success) {
      setLoadedFiles((current) =>
        shouldReplaceProjectRevealFiles(current, inlineFiles)
          ? inlineFiles
          : current,
      );
      setBinaryFiles((current) =>
        areStringRecordMapsEqual(current, EMPTY_BINARY_FILES)
          ? current
          : EMPTY_BINARY_FILES,
      );
      return;
    }

    const cached = getCachedProjectRevealFiles(projectAutoOpenKey);
    const nextLoadedFiles = cached?.files || null;
    const nextBinaryFiles = normalizeProjectRevealBinaryFiles(
      cached?.binaryFiles,
    );
    setLoadedFiles((current) =>
      shouldReplaceProjectRevealFiles(current, nextLoadedFiles)
        ? nextLoadedFiles
        : current,
    );
    setBinaryFiles((current) =>
      areStringRecordMapsEqual(current, nextBinaryFiles)
        ? current
        : nextBinaryFiles,
    );

    let cancelled = false;
    void loadProjectRevealFilesCached({
      previewKey: projectAutoOpenKey,
      project: parsed,
    })
      .then(({ files, binaryFiles: loadedBinaryFiles }) => {
        if (cancelled) return;
        const nextBinaryFiles =
          normalizeProjectRevealBinaryFiles(loadedBinaryFiles);
        setLoadedFiles((current) =>
          shouldReplaceProjectRevealFiles(current, files) ? files : current,
        );
        setBinaryFiles((current) =>
          areStringRecordMapsEqual(current, nextBinaryFiles)
            ? current
            : nextBinaryFiles,
        );
      })
      .catch(() => {
        if (!cancelled) {
          // File loading failed silently; preview panel will handle retry
        }
      });

    return () => {
      cancelled = true;
    };
  }, [parsed, projectAutoOpenKey, success, inlineFiles]);

  if (isPending) {
    return (
      <div className="my-2 flex items-center gap-3 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-4 py-3 shadow-[0_4px_12px_rgba(18,38,63,0.03)] dark:bg-stone-900">
        <div className="p-2.5 rounded-lg bg-stone-100 dark:bg-stone-800">
          <LoadingSpinner
            size="sm"
            className="text-stone-600 dark:text-stone-400"
          />
        </div>
        <div className="flex-1 min-w-0">
          <div className="truncate text-sm font-medium text-[var(--theme-text)]">
            {projectName || t("project.loading")}
          </div>
          <div className="mt-0.5 truncate text-xs text-[var(--theme-text-secondary)]">
            {(args.project_path as string) || ""}
          </div>
        </div>
        <div className="text-xs text-amber-600 dark:text-amber-400">
          {t("chat.message.running")}
        </div>
      </div>
    );
  }

  if (cancelled && !result) {
    return (
      <div className="my-2 flex items-center gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 dark:border-amber-800 dark:bg-amber-900/20">
        <div className="p-2.5 rounded-lg bg-amber-100 dark:bg-amber-900/30">
          <Code2 size={20} className="text-amber-500" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-stone-700 dark:text-stone-300 truncate">
            {projectName || t("project.loading")}
          </div>
          <div className="text-xs text-stone-500 dark:text-stone-400 truncate mt-0.5">
            {(args.project_path as string) || ""}
          </div>
        </div>
        <div className="text-xs text-amber-600 dark:text-amber-400">
          {t("chat.message.cancelled")}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="my-2 flex items-center gap-3 rounded-lg border border-red-200 bg-red-50 px-4 py-3 dark:border-red-800 dark:bg-red-900/20">
        <div className="p-2.5 rounded-lg bg-red-100 dark:bg-red-900/30">
          <Code2 size={20} className="text-red-500" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-red-700 dark:text-red-300 truncate">
            {projectName || t("project.error")}
          </div>
          <div className="text-xs text-red-500 dark:text-red-400 truncate mt-0.5">
            {error}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="my-2 sm:my-3 min-w-0">
      <div
        className="flex cursor-pointer items-center gap-3 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-3 py-2.5 shadow-[0_4px_12px_rgba(18,38,63,0.03)] transition-colors duration-150 hover:bg-[var(--theme-bg-sidebar)] dark:bg-stone-900 sm:px-4 sm:py-3"
        onClick={() => openPreview()}
      >
        <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-blue-100 dark:bg-blue-900/40">
          {mode === "folder" ? (
            <FolderTree
              size={20}
              className="text-blue-600 dark:text-blue-400"
            />
          ) : (
            <Code2 size={20} className="text-blue-600 dark:text-blue-400" />
          )}
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-stone-800 dark:text-stone-100 truncate">
            {projectName || t("project.untitled")}
          </div>
          <div className="text-xs text-stone-400 dark:text-stone-500 mt-0.5 truncate">
            {t("project.fileCount", { count: fileCount })}
            {mode === "project" && template !== "static"
              ? ` · ${template}`
              : ""}
          </div>
        </div>
        <div className="shrink-0 flex items-center gap-1 relative z-10">
          {loadedFiles && (
            <button
              onClick={(event) => {
                event.stopPropagation();
                exportProjectZip(loadedFiles, projectName, binaryFiles);
              }}
              className="flex items-center justify-center size-8 rounded-lg text-stone-400 dark:text-stone-500 hover:text-stone-600 dark:hover:text-stone-300 hover:bg-stone-100 dark:hover:bg-stone-700 transition-colors"
              title={t("project.exportZip")}
            >
              <Download size={16} />
            </button>
          )}
          <button
            onClick={(event) => {
              event.stopPropagation();
              openPreview();
            }}
            className="px-3 py-1.5 rounded-lg text-xs font-medium bg-stone-100 dark:bg-stone-800 text-stone-700 dark:text-stone-300 hover:bg-stone-200 dark:hover:bg-stone-700 transition-colors"
          >
            {t("project.preview", "预览")}
          </button>
        </div>
      </div>
    </div>
  );
}
