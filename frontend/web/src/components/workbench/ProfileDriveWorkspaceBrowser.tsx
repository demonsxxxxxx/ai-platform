import {
  Building2,
  ChevronDown,
  ChevronRight,
  Download,
  Eye,
  FileText,
  FolderClosed,
  FolderOpen,
  Paperclip,
  RefreshCw,
  Search,
  UserRound,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import toast from "react-hot-toast";

import type { SessionInputFile } from "../../services/api";
import {
  PROFILE_DRIVE_CONNECTION_CHANGED_EVENT,
  profileDriveApi,
  ProfileDriveRequestError,
  type ProfileDriveFileEntry,
  type ProfileDriveFileReference,
  type ProfileDriveSourceId,
} from "../../services/api/profileDrive";
import { sessionApi } from "../../services/api/session";
import { formatFileSize, getFileExtension } from "../documents/utils";
import {
  PROFILE_DRIVE_DRAG_TYPE,
  serializeProfileDriveDragReference,
} from "./profileDriveDrag";
import { workbenchSurface } from "./workbenchSurface";

const PROFILE_DRIVE_PREVIEW_EXTENSIONS = new Set([
  "avif",
  "bmp",
  "csv",
  "doc",
  "docx",
  "gif",
  "jpeg",
  "jpg",
  "json",
  "markdown",
  "md",
  "pdf",
  "png",
  "pptx",
  "tif",
  "tiff",
  "txt",
  "webp",
  "xlsx",
]);

const PROFILE_DIRECTORY_LABELS: Record<string, string> = {
  contacts: "联系人",
  desktop: "桌面",
  documents: "文档",
  downloads: "下载",
  favorites: "收藏夹",
  links: "链接",
  music: "音乐",
  pictures: "图片",
  "saved games": "已保存的游戏",
  searches: "搜索",
  "start menu": "开始菜单",
  videos: "视频",
};

function visibleEntry(entry: ProfileDriveFileEntry): boolean {
  const name = entry.name.toLowerCase();
  return !name.startsWith("~$") && name !== "$recycle.bin";
}

function previewableEntry(entry: ProfileDriveFileEntry): boolean {
  return PROFILE_DRIVE_PREVIEW_EXTENSIONS.has(getFileExtension(entry.name));
}

interface ProfileDriveWorkspaceBrowserProps {
  sessionId: string | null;
  onImported: (file: SessionInputFile) => void;
  onAddToConversation: (
    reference: ProfileDriveFileReference,
  ) => SessionInputFile | void | Promise<SessionInputFile | void>;
}

interface ProfileDriveSourceBrowserProps extends ProfileDriveWorkspaceBrowserProps {
  sourceId: ProfileDriveSourceId;
  title: string;
  onControlsChange: (
    sourceId: ProfileDriveSourceId,
    controls: ProfileDriveSourceControls,
  ) => void;
}

interface ProfileDriveSourceControls {
  loading: boolean;
  connected: boolean | null;
  error: string | null;
  visibleEntryCount: number;
  refresh: () => Promise<void>;
}

interface LoadedDirectory {
  entries: ProfileDriveFileEntry[];
  truncated: boolean;
}

interface ProfileDriveTreeEntryRow {
  kind: "entry";
  entry: ProfileDriveFileEntry;
  depth: number;
  parentPath: string;
}

interface ProfileDriveTreeStatusRow {
  kind: "status";
  key: string;
  depth: number;
  text: string;
}

interface ProfileDriveTreeErrorRow {
  kind: "error";
  key: string;
  depth: number;
  path: string;
  label: string;
  text: string;
}

type ProfileDriveTreeRow =
  | ProfileDriveTreeEntryRow
  | ProfileDriveTreeStatusRow
  | ProfileDriveTreeErrorRow;

function profileDirectoryLabel(name: string): string {
  return PROFILE_DIRECTORY_LABELS[name.toLowerCase()] ?? name;
}

function entryLabel(
  entry: ProfileDriveFileEntry,
  path: string,
  sourceId: ProfileDriveSourceId,
): string {
  return sourceId === "profile" && path === "" && entry.type === "directory"
    ? profileDirectoryLabel(entry.name)
    : entry.name;
}

function requiresReauthentication(error: unknown): boolean {
  return (
    error instanceof ProfileDriveRequestError &&
    ["reauth_required", "credential_rejected"].includes(error.code)
  );
}

function browserError(error: unknown, title: string): string {
  if (requiresReauthentication(error)) {
    return `${title}服务器需要重新认证。`;
  }
  return `${title}暂时不可用。`;
}

function directoryError(error: unknown): string {
  if (error instanceof ProfileDriveRequestError) {
    if (error.code === "access_denied") return "没有权限访问此文件夹。";
    if (error.code === "path_not_found") return "此文件夹不存在或已被移除。";
  }
  return "暂时无法打开此文件夹。";
}

const noopRefresh = async () => undefined;

function ProfileDriveSourceBrowser({
  sessionId,
  sourceId,
  title,
  onImported,
  onAddToConversation,
  onControlsChange,
}: ProfileDriveSourceBrowserProps) {
  const [connected, setConnected] = useState<boolean | null>(null);
  const [entries, setEntries] = useState<ProfileDriveFileEntry[]>([]);
  const [childrenByPath, setChildrenByPath] = useState(
    () => new Map<string, LoadedDirectory>(),
  );
  const [expandedPaths, setExpandedPaths] = useState(() => new Set<string>());
  const [loadingPaths, setLoadingPaths] = useState(() => new Set<string>());
  const [directoryErrors, setDirectoryErrors] = useState(
    () => new Map<string, string>(),
  );
  const [filter, setFilter] = useState("");
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [importingPath, setImportingPath] = useState<string | null>(null);
  const treeGenerationRef = useRef(0);
  const pendingPathsRef = useRef(new Set<string>());
  const directoryButtonRefs = useRef(new Map<string, HTMLButtonElement>());
  const mountedRef = useRef(false);

  const loadRoot = useCallback(async () => {
    const generation = ++treeGenerationRef.current;
    pendingPathsRef.current.clear();
    setLoading(true);
    setError(null);
    setExpandedPaths(new Set());
    setChildrenByPath(new Map());
    setLoadingPaths(new Set());
    setDirectoryErrors(new Map());
    try {
      const result = await profileDriveApi.listFiles("", sourceId);
      if (generation !== treeGenerationRef.current) return;
      setEntries(result.entries.filter(visibleEntry));
      setTruncated(result.truncated);
      setConnected(true);
    } catch (loadError) {
      if (generation !== treeGenerationRef.current) return;
      setEntries([]);
      setTruncated(false);
      setError(browserError(loadError, title));
    } finally {
      if (generation === treeGenerationRef.current) setLoading(false);
    }
  }, [sourceId, title]);

  const closeDirectory = useCallback((path: string) => {
    setExpandedPaths((current) => {
      const next = new Set(current);
      next.delete(path);
      return next;
    });
    setDirectoryErrors((current) => {
      if (!current.has(path)) return current;
      const next = new Map(current);
      next.delete(path);
      return next;
    });
  }, []);

  const returnFromDirectory = useCallback(
    (path: string) => {
      directoryButtonRefs.current.get(path)?.focus();
      closeDirectory(path);
    },
    [closeDirectory],
  );

  const toggleDirectory = useCallback(
    async (entry: ProfileDriveFileEntry) => {
      if (expandedPaths.has(entry.path)) {
        closeDirectory(entry.path);
        return;
      }

      setExpandedPaths((current) => new Set(current).add(entry.path));
      setDirectoryErrors((current) => {
        if (!current.has(entry.path)) return current;
        const next = new Map(current);
        next.delete(entry.path);
        return next;
      });
      if (childrenByPath.has(entry.path) || pendingPathsRef.current.has(entry.path)) {
        return;
      }

      const generation = treeGenerationRef.current;
      pendingPathsRef.current.add(entry.path);
      setLoadingPaths((current) => new Set(current).add(entry.path));
      try {
        const result = await profileDriveApi.listFiles(entry.path, sourceId);
        if (generation !== treeGenerationRef.current) return;
        setChildrenByPath((current) => {
          const next = new Map(current);
          next.set(entry.path, {
            entries: result.entries.filter(visibleEntry),
            truncated: result.truncated,
          });
          return next;
        });
      } catch (loadError) {
        if (generation !== treeGenerationRef.current) return;
        if (requiresReauthentication(loadError)) {
          closeDirectory(entry.path);
          setError(browserError(loadError, title));
        } else {
          setDirectoryErrors((current) =>
            new Map(current).set(entry.path, directoryError(loadError)),
          );
        }
      } finally {
        if (generation === treeGenerationRef.current) {
          pendingPathsRef.current.delete(entry.path);
          setLoadingPaths((current) => {
            const next = new Set(current);
            next.delete(entry.path);
            return next;
          });
        }
      }
    },
    [childrenByPath, closeDirectory, expandedPaths, sourceId, title],
  );

  useEffect(() => {
    let active = true;
    const statusGeneration = treeGenerationRef.current;
    const pendingPaths = pendingPathsRef.current;
    mountedRef.current = true;
    setLoading(true);
    void profileDriveApi
      .status()
      .then((status) => {
        if (!active || statusGeneration !== treeGenerationRef.current) return;
        setConnected(status.connected);
        if (status.connected) return loadRoot();
        setLoading(false);
        return undefined;
      })
      .catch((statusError) => {
        if (!active || statusGeneration !== treeGenerationRef.current) return;
        setConnected(false);
        setError(browserError(statusError, title));
        setLoading(false);
      });
    return () => {
      active = false;
      mountedRef.current = false;
      treeGenerationRef.current += 1;
      pendingPaths.clear();
    };
  }, [loadRoot, title]);

  useEffect(() => {
    const handleConnectionChange = () => {
      setConnected(true);
      void loadRoot();
    };
    window.addEventListener(
      PROFILE_DRIVE_CONNECTION_CHANGED_EVENT,
      handleConnectionChange,
    );
    return () =>
      window.removeEventListener(
        PROFILE_DRIVE_CONNECTION_CHANGED_EVENT,
        handleConnectionChange,
      );
  }, [loadRoot]);

  const preview = useCallback(
    async (entry: ProfileDriveFileEntry) => {
      if (importingPath) return;
      setImportingPath(entry.path);
      try {
        const reference = {
          source_id: sourceId,
          path: entry.path,
        } satisfies ProfileDriveFileReference;
        const imported = sessionId
          ? await sessionApi.importProfileDriveFile(sessionId, reference)
          : await onAddToConversation(reference);
        if (imported && mountedRef.current) onImported(imported);
      } catch (importError) {
        if (!mountedRef.current) return;
        console.error("[ProfileDrive] import failed", {
          kind:
            importError instanceof ProfileDriveRequestError
              ? importError.code
              : "request_failed",
        });
        toast.error("文件导入工作区失败。");
      } finally {
        if (mountedRef.current) setImportingPath(null);
      }
    },
    [importingPath, onAddToConversation, onImported, sessionId, sourceId],
  );

  const addToConversation = useCallback(
    async (entry: ProfileDriveFileEntry) => {
      if (importingPath) return;
      setImportingPath(entry.path);
      try {
        await onAddToConversation({
          source_id: sourceId,
          path: entry.path,
        });
      } finally {
        if (mountedRef.current) setImportingPath(null);
      }
    },
    [importingPath, onAddToConversation, sourceId],
  );

  const treeRows: ProfileDriveTreeRow[] = [];
  const appendRows = (
    directoryEntries: ProfileDriveFileEntry[],
    depth: number,
    parentPath: string,
  ): void => {
    for (const entry of directoryEntries) {
      treeRows.push({ kind: "entry", entry, depth, parentPath });
      if (entry.type !== "directory" || !expandedPaths.has(entry.path)) continue;
      if (loadingPaths.has(entry.path)) {
        treeRows.push({
          kind: "status",
          key: `${entry.path}:loading`,
          depth: depth + 1,
          text: "正在加载…",
        });
        continue;
      }
      const directoryFailure = directoryErrors.get(entry.path);
      if (directoryFailure) {
        treeRows.push({
          kind: "error",
          key: `${entry.path}:error`,
          depth: depth + 1,
          path: entry.path,
          label: entryLabel(entry, parentPath, sourceId),
          text: directoryFailure,
        });
        continue;
      }
      const loaded = childrenByPath.get(entry.path);
      if (!loaded) continue;
      if (loaded.entries.length === 0) {
        treeRows.push({
          kind: "status",
          key: `${entry.path}:empty`,
          depth: depth + 1,
          text: "目录为空",
        });
      } else {
        appendRows(loaded.entries, depth + 1, entry.path);
      }
      if (loaded.truncated) {
        treeRows.push({
          kind: "status",
          key: `${entry.path}:truncated`,
          depth: depth + 1,
          text: "目录内容已截断",
        });
      }
    }
  };
  appendRows(entries, 0, "");

  const normalizedFilter = filter.trim().toLocaleLowerCase();
  const matchingPaths = normalizedFilter
    ? new Set(
        treeRows.flatMap((row) =>
          row.kind === "entry" &&
          entryLabel(row.entry, row.parentPath, sourceId)
            .toLocaleLowerCase()
            .includes(normalizedFilter)
            ? [row.entry.path]
            : [],
        ),
      )
    : null;
  const filteredRows = matchingPaths
    ? treeRows.filter((row) =>
        row.kind === "entry"
          ? matchingPaths.has(row.entry.path)
          : row.kind === "error" && matchingPaths.has(row.path),
      )
    : treeRows;
  const visibleEntryCount = filteredRows.filter(
    (row) => row.kind === "entry",
  ).length;

  useEffect(() => {
    onControlsChange(sourceId, {
      loading,
      connected,
      error,
      visibleEntryCount,
      refresh: loadRoot,
    });
  }, [
    connected,
    error,
    loadRoot,
    loading,
    onControlsChange,
    sourceId,
    visibleEntryCount,
  ]);

  return (
    <div data-profile-drive-source={sourceId}>
      {connected !== false && !error && !loading && entries.length > 0 && (
        <label className="mt-2 flex h-8 items-center gap-2 rounded-md bg-[var(--theme-bg-sidebar)] px-2 ring-1 ring-[var(--theme-border)] focus-within:ring-2 focus-within:ring-[var(--theme-primary)]">
          <Search
            size={13}
            className="shrink-0 text-[var(--theme-text-tertiary)]"
            aria-hidden="true"
          />
          <input
            type="search"
            aria-label="筛选文件树"
            className="min-w-0 flex-1 bg-transparent text-xs text-[var(--theme-text)] outline-none placeholder:text-[var(--theme-text-tertiary)]"
            placeholder="筛选文件树"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          />
        </label>
      )}

      <div className="mt-2">
        {error ? (
          <p role="status" className="text-xs leading-5 text-[var(--theme-danger)]">
            {error}
          </p>
        ) : connected === false ? (
          <p className={workbenchSurface.mutedText}>{title}服务器未连接。</p>
        ) : loading ? (
          <p className={workbenchSurface.mutedText}>正在加载{title}…</p>
        ) : entries.length === 0 ? (
          <p className={workbenchSurface.mutedText}>此目录为空。</p>
        ) : filteredRows.length === 0 ? (
          <p className={workbenchSurface.mutedText}>文件树中没有匹配项。</p>
        ) : (
          <div
            role="tree"
            aria-label={`${title}目录`}
            className="min-w-0 max-w-full space-y-0.5 overflow-hidden"
          >
            {filteredRows.map((row) => {
              if (row.kind === "error") {
                return (
                  <div
                    key={row.key}
                    role="alert"
                    className="flex min-h-8 min-w-0 items-center gap-2 py-1 text-[11px]"
                    style={{ paddingInlineStart: `${22 + row.depth * 16}px` }}
                  >
                    <span className="min-w-0 flex-1 text-[var(--theme-danger)]">
                      {row.text}
                    </span>
                    <button
                      type="button"
                      aria-label={`返回 ${row.label}`}
                      className="shrink-0 rounded px-1.5 py-1 font-medium text-[var(--theme-primary)] hover:bg-[var(--theme-workbench-panel)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-primary)]"
                      onClick={() => returnFromDirectory(row.path)}
                    >
                      返回
                    </button>
                  </div>
                );
              }

              if (row.kind === "status") {
                return (
                  <p
                    key={row.key}
                    role="status"
                    className="flex h-7 items-center truncate text-[11px] text-[var(--theme-text-tertiary)]"
                    style={{ paddingInlineStart: `${22 + row.depth * 16}px` }}
                  >
                    {row.text}
                  </p>
                );
              }

              const { entry, depth, parentPath } = row;
              const directory = entry.type === "directory";
              const expanded = directory && expandedPaths.has(entry.path);
              const folderLoading = directory && loadingPaths.has(entry.path);
              const previewable = !directory && previewableEntry(entry);
              const importing = importingPath === entry.path;
              const disabled = !directory && Boolean(importingPath);
              const name = entryLabel(entry, parentPath, sourceId);
              const actionLabel = directory
                ? `${expanded ? "收起" : "展开"}文件夹 ${name}`
                : `${previewable ? "预览" : "下载"} ${name}`;
              const ActionIcon = previewable ? Eye : Download;
              const ExpandIcon = expanded ? ChevronDown : ChevronRight;
              return (
                <div
                  key={`${entry.type}:${entry.path}`}
                  role="treeitem"
                  aria-level={depth + 1}
                  aria-expanded={directory ? expanded : undefined}
                  className={`group flex h-8 w-full min-w-0 max-w-full items-center overflow-hidden rounded hover:bg-[var(--theme-workbench-panel)] ${
                    !directory && !importingPath ? "cursor-grab active:cursor-grabbing" : ""
                  }`}
                  draggable={!directory && !importingPath}
                  onDragStart={(event) => {
                    if (directory || importingPath) return;
                    event.dataTransfer.effectAllowed = "copy";
                    event.dataTransfer.setData(
                      PROFILE_DRIVE_DRAG_TYPE,
                      serializeProfileDriveDragReference({
                        source_id: sourceId,
                        path: entry.path,
                      }),
                    );
                  }}
                >
                  <button
                    ref={
                      directory
                        ? (node) => {
                            if (node) directoryButtonRefs.current.set(entry.path, node);
                            else directoryButtonRefs.current.delete(entry.path);
                          }
                        : undefined
                    }
                    type="button"
                    className="flex h-full min-w-0 flex-1 items-center gap-1.5 overflow-hidden rounded px-1.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-primary)] disabled:cursor-default disabled:opacity-60"
                    style={{ paddingInlineStart: `${6 + depth * 16}px` }}
                    aria-label={actionLabel}
                    aria-expanded={directory ? expanded : undefined}
                    title={actionLabel}
                    disabled={disabled}
                    onClick={() =>
                      directory ? void toggleDirectory(entry) : void preview(entry)
                    }
                  >
                    {directory ? (
                      <ExpandIcon
                        size={13}
                        className="shrink-0 text-[var(--theme-text-tertiary)]"
                        aria-hidden="true"
                      />
                    ) : (
                      <span className="w-[13px] shrink-0" aria-hidden="true" />
                    )}
                    {directory ? (
                      expanded ? (
                        <FolderOpen
                          size={14}
                          className="shrink-0 text-[var(--theme-text-tertiary)]"
                          aria-hidden="true"
                        />
                      ) : (
                        <FolderClosed
                          size={14}
                          className="shrink-0 text-[var(--theme-text-tertiary)]"
                          aria-hidden="true"
                        />
                      )
                    ) : (
                      <FileText
                        size={14}
                        className="shrink-0 text-[var(--theme-text-tertiary)]"
                        aria-hidden="true"
                      />
                    )}
                    <span className="min-w-0 flex-1 truncate text-xs font-medium text-[var(--theme-text)]">
                      {name}
                    </span>
                    {folderLoading && (
                      <RefreshCw
                        size={12}
                        className="shrink-0 animate-spin text-[var(--theme-text-tertiary)]"
                        aria-hidden="true"
                      />
                    )}
                    {!directory && entry.size !== null && (
                      <span className="shrink-0 text-[10px] text-[var(--theme-text-tertiary)]">
                        {formatFileSize(entry.size)}
                      </span>
                    )}
                    {!directory && (
                      <ActionIcon
                        size={13}
                        aria-hidden="true"
                        className={`shrink-0 text-[var(--theme-text-tertiary)] ${
                          importing ? "animate-pulse" : ""
                        }`}
                      />
                    )}
                  </button>
                  {!directory && (
                    <button
                      type="button"
                      aria-label={`添加 ${name} 到会话`}
                      title="添加到会话"
                      disabled={disabled}
                      onClick={() => void addToConversation(entry)}
                      className="mr-0.5 flex size-7 shrink-0 items-center justify-center rounded text-[var(--theme-text-tertiary)] hover:text-[var(--theme-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-primary)] disabled:cursor-default disabled:opacity-60"
                    >
                      <Paperclip size={13} aria-hidden="true" />
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}
        {truncated && !loading && (
          <p role="status" className="mt-2 text-[11px] text-[var(--theme-text-tertiary)]">
            目录内容已截断。
          </p>
        )}
      </div>
    </div>
  );
}

const DRIVE_TABS = [
  { id: "profile", label: "本地文件", Icon: UserRound },
  { id: "public", label: "公盘文件", Icon: Building2 },
] as const;

export function ProfileDriveWorkspaceBrowser({
  sessionId,
  onImported,
  onAddToConversation,
}: ProfileDriveWorkspaceBrowserProps) {
  const [activeSource, setActiveSource] =
    useState<ProfileDriveSourceId>("profile");
  const [sourceControls, setSourceControls] = useState<
    Record<ProfileDriveSourceId, ProfileDriveSourceControls>
  >(() => ({
    profile: {
      loading: true,
      connected: null,
      error: null,
      visibleEntryCount: 0,
      refresh: noopRefresh,
    },
    public: {
      loading: true,
      connected: null,
      error: null,
      visibleEntryCount: 0,
      refresh: noopRefresh,
    },
  }));
  const handleControlsChange = useCallback(
    (
      sourceId: ProfileDriveSourceId,
      controls: ProfileDriveSourceControls,
    ) => {
      setSourceControls((current) => {
        const previous = current[sourceId];
        if (
          previous.loading === controls.loading &&
          previous.connected === controls.connected &&
          previous.error === controls.error &&
          previous.visibleEntryCount === controls.visibleEntryCount &&
          previous.refresh === controls.refresh
        ) {
          return current;
        }
        return { ...current, [sourceId]: controls };
      });
    },
    [],
  );
  const activeTab = DRIVE_TABS.find(({ id }) => id === activeSource)!;
  const activeControls = sourceControls[activeSource];

  return (
    <section
      data-librechat-context-section="files"
      aria-labelledby="librechat-drive-files-label"
      className={`${workbenchSurface.compactPanel} mt-3 overflow-hidden`}
    >
      <div className="p-3 pb-0">
        <div className="flex min-w-0 items-center gap-2">
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <span className={workbenchSurface.catalog.compactIconBox}>
              <FolderOpen size={15} aria-hidden="true" />
            </span>
            <h3
              id="librechat-drive-files-label"
              className="truncate text-xs font-semibold text-[var(--theme-text)]"
            >
              文件
            </h3>
          </div>
          <button
            type="button"
            className="flex size-6 shrink-0 items-center justify-center rounded text-[var(--theme-text-tertiary)] hover:bg-[var(--theme-workbench-panel)] hover:text-[var(--theme-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-primary)] disabled:opacity-50"
            aria-label={`刷新${activeTab.label}`}
            title={`刷新${activeTab.label}`}
            disabled={activeControls.loading || activeControls.connected === false}
            onClick={() => void activeControls.refresh()}
          >
            <RefreshCw
              size={13}
              className={activeControls.loading ? "animate-spin" : ""}
            />
          </button>
          <span
            className={workbenchSurface.catalog.chip}
            data-profile-drive-count={activeSource}
            aria-label={`${activeTab.label}可见条目数：${
              activeControls.loading
                ? "加载中"
                : activeControls.error || activeControls.connected !== true
                  ? "不可用"
                  : activeControls.visibleEntryCount
            }`}
            aria-live="polite"
          >
            {activeControls.loading
              ? "…"
              : activeControls.error || !activeControls.connected
                ? "!"
                : activeControls.visibleEntryCount}
          </span>
        </div>
        <div
          role="tablist"
          aria-label="文件盘"
          className="mt-3 grid h-9 grid-cols-2 gap-0.5 rounded-md bg-[var(--theme-bg-sidebar)] p-0.5 ring-1 ring-[var(--theme-border)]"
        >
          {DRIVE_TABS.map(({ id, label, Icon }) => {
            const active = activeSource === id;
            return (
              <button
                key={id}
                id={`profile-drive-${id}-tab`}
                type="button"
                role="tab"
                aria-selected={active}
                aria-controls={`profile-drive-${id}-panel`}
                data-active={active ? "true" : "false"}
                className={`flex h-8 min-w-0 items-center justify-center gap-1.5 rounded px-2 text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-primary)] ${
                  active
                    ? "bg-[var(--theme-workbench-canvas)] text-[var(--theme-text)] ring-1 ring-[var(--theme-border)]"
                    : "text-[var(--theme-text-secondary)] hover:bg-[var(--theme-workbench-panel)] hover:text-[var(--theme-text)]"
                }`}
                onClick={() => setActiveSource(id)}
              >
                <Icon size={14} className="shrink-0" aria-hidden="true" />
                <span className="truncate">{label}</span>
              </button>
            );
          })}
        </div>
      </div>

      {DRIVE_TABS.map(({ id, label }) => (
        <div
          key={id}
          id={`profile-drive-${id}-panel`}
          role="tabpanel"
          aria-labelledby={`profile-drive-${id}-tab`}
          hidden={activeSource !== id}
          className="px-3 pb-3"
        >
          <ProfileDriveSourceBrowser
            sessionId={sessionId}
            sourceId={id}
            title={label}
            onImported={onImported}
            onAddToConversation={onAddToConversation}
            onControlsChange={handleControlsChange}
          />
        </div>
      ))}
    </section>
  );
}
