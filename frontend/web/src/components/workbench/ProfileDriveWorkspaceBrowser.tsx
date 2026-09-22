import {
  ChevronRight,
  Download,
  Eye,
  FileText,
  Paperclip,
  RefreshCw,
  Search,
  Server,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import toast from "react-hot-toast";

import type { SessionInputFile } from "../../services/api";
import {
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
  sourceId?: ProfileDriveSourceId;
  title?: string;
  onImported: (file: SessionInputFile) => void;
  onAddToConversation: (reference: ProfileDriveFileReference) => void | Promise<void>;
}

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

function browserError(error: unknown, title: string): string {
  if (
    error instanceof ProfileDriveRequestError &&
    ["reauth_required", "credential_rejected"].includes(error.code)
  ) {
    return `${title}服务器需要重新认证。`;
  }
  return `${title}暂时不可用。`;
}

export function ProfileDriveWorkspaceBrowser({
  sessionId,
  sourceId = "profile",
  title = "个人文件",
  onImported,
  onAddToConversation,
}: ProfileDriveWorkspaceBrowserProps) {
  const [connected, setConnected] = useState<boolean | null>(null);
  const [path, setPath] = useState("");
  const [entries, setEntries] = useState<ProfileDriveFileEntry[]>([]);
  const [filter, setFilter] = useState("");
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [importingPath, setImportingPath] = useState<string | null>(null);
  const requestIdRef = useRef(0);
  const mountedRef = useRef(false);

  const load = useCallback(async (nextPath: string) => {
    const requestId = ++requestIdRef.current;
    setLoading(true);
    setError(null);
    try {
      const result = await profileDriveApi.listFiles(nextPath, sourceId);
      if (requestId !== requestIdRef.current) return;
      setPath(result.path);
      setEntries(result.entries.filter(visibleEntry));
      setTruncated(result.truncated);
      setConnected(true);
    } catch (loadError) {
      if (requestId !== requestIdRef.current) return;
      setEntries([]);
      setTruncated(false);
      setError(browserError(loadError, title));
    } finally {
      if (requestId === requestIdRef.current) setLoading(false);
    }
  }, [sourceId, title]);

  const navigate = useCallback(
    (nextPath: string) => {
      setFilter("");
      return load(nextPath);
    },
    [load],
  );

  useEffect(() => {
    let active = true;
    mountedRef.current = true;
    setLoading(true);
    void profileDriveApi
      .status()
      .then((status) => {
        if (!active) return;
        setConnected(status.connected);
        if (status.connected) return load("");
        setLoading(false);
        return undefined;
      })
      .catch((statusError) => {
        if (!active) return;
        setConnected(false);
        setError(browserError(statusError, title));
        setLoading(false);
      });
    return () => {
      active = false;
      mountedRef.current = false;
      requestIdRef.current += 1;
    };
  }, [load, title]);

  const preview = useCallback(
    async (entry: ProfileDriveFileEntry) => {
      if (!sessionId || importingPath) return;
      setImportingPath(entry.path);
      try {
        const imported = await sessionApi.importProfileDriveFile(sessionId, {
          source_id: sourceId,
          path: entry.path,
        });
        if (mountedRef.current) onImported(imported);
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
    [importingPath, onImported, sessionId, sourceId],
  );

  const normalizedFilter = filter.trim().toLocaleLowerCase();
  const filteredEntries = normalizedFilter
    ? entries.filter((entry) =>
        entryLabel(entry, path, sourceId)
          .toLocaleLowerCase()
          .includes(normalizedFilter),
      )
    : entries;
  const pathSegments = path ? path.split("/") : [];
  const breadcrumbs = [
    { label: title, path: "" },
    ...pathSegments.map((segment, index) => ({
      label:
        sourceId === "profile" && index === 0
          ? profileDirectoryLabel(segment)
          : segment,
      path: pathSegments.slice(0, index + 1).join("/"),
    })),
  ];

  return (
    <section
      data-librechat-context-section={`${sourceId}-drive`}
      aria-labelledby={`librechat-${sourceId}-drive-label`}
      className={`${workbenchSurface.compactPanel} mt-3 p-3`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className={workbenchSurface.catalog.compactIconBox}>
            <Server size={15} aria-hidden="true" />
          </span>
          <h3
            id={`librechat-${sourceId}-drive-label`}
            className="truncate text-xs font-semibold text-[var(--theme-text)]"
          >
            {title}
          </h3>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            className="rounded p-1 text-[var(--theme-text-tertiary)] hover:bg-[var(--theme-workbench-panel)] hover:text-[var(--theme-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-primary)] disabled:opacity-50"
            aria-label={`刷新${title}`}
            title={`刷新${title}`}
            disabled={loading || connected === false}
            onClick={() => void load(path)}
          >
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
          </button>
          <span className={workbenchSurface.catalog.chip}>
            {loading ? "…" : connected ? filteredEntries.length : "!"}
          </span>
        </div>
      </div>

      {connected !== false && !error && (
        <>
          <nav
            aria-label={`${title}路径`}
            className="mt-2 flex min-h-7 items-center overflow-x-auto whitespace-nowrap border-y border-[var(--theme-border)] py-1 text-[11px]"
          >
            {breadcrumbs.map((breadcrumb, index) => {
              const current = index === breadcrumbs.length - 1;
              return (
                <span
                  key={breadcrumb.path || "root"}
                  className="flex min-w-0 items-center"
                >
                  {index > 0 && (
                    <ChevronRight
                      size={12}
                      className="shrink-0 text-[var(--theme-text-tertiary)]"
                      aria-hidden="true"
                    />
                  )}
                  {current ? (
                    <span
                      aria-current="page"
                      className="max-w-32 truncate px-1 font-medium text-[var(--theme-text)]"
                      title={breadcrumb.path || title}
                    >
                      {breadcrumb.label}
                    </span>
                  ) : (
                    <button
                      type="button"
                      className="max-w-32 truncate rounded px-1 text-[var(--theme-text-secondary)] hover:bg-[var(--theme-workbench-panel)] hover:text-[var(--theme-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-primary)]"
                      title={breadcrumb.path || title}
                      onClick={() => void navigate(breadcrumb.path)}
                    >
                      {breadcrumb.label}
                    </button>
                  )}
                </span>
              );
            })}
          </nav>

          {!loading && entries.length > 0 && (
            <label className="mt-2 flex h-8 items-center gap-2 rounded-md bg-[var(--theme-bg-sidebar)] px-2 ring-1 ring-[var(--theme-border)] focus-within:ring-2 focus-within:ring-[var(--theme-primary)]">
              <Search
                size={13}
                className="shrink-0 text-[var(--theme-text-tertiary)]"
                aria-hidden="true"
              />
              <input
                type="search"
                aria-label="筛选当前目录"
                className="min-w-0 flex-1 bg-transparent text-xs text-[var(--theme-text)] outline-none placeholder:text-[var(--theme-text-tertiary)]"
                placeholder="筛选当前目录"
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
              />
            </label>
          )}
        </>
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
        ) : filteredEntries.length === 0 ? (
          <p className={workbenchSurface.mutedText}>当前目录没有匹配项。</p>
        ) : (
          <div role="tree" aria-label={`${title}目录`} className="space-y-0.5">
            {filteredEntries.map((entry) => {
              const directory = entry.type === "directory";
              const previewable = !directory && previewableEntry(entry);
              const importing = importingPath === entry.path;
              const disabled = Boolean(importingPath) || (!directory && !sessionId);
              const name = entryLabel(entry, path, sourceId);
              const actionLabel = directory
                ? `打开文件夹 ${name}`
                : `${previewable ? "预览" : "下载"} ${name}`;
              const ActionIcon = previewable ? Eye : Download;
              return (
                <div
                  key={`${entry.type}:${entry.path}`}
                  role="treeitem"
                  className={`group flex h-8 w-full min-w-0 items-center rounded hover:bg-[var(--theme-workbench-panel)] ${
                    !directory && sessionId ? "cursor-grab active:cursor-grabbing" : ""
                  }`}
                  draggable={!directory && Boolean(sessionId) && !importing}
                  onDragStart={(event) => {
                    if (directory) return;
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
                    type="button"
                    className="flex h-full min-w-0 flex-1 items-center gap-2 rounded px-1.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-primary)] disabled:cursor-default disabled:opacity-60"
                    aria-label={actionLabel}
                    title={actionLabel}
                    disabled={disabled}
                    onClick={() =>
                      directory ? void navigate(entry.path) : void preview(entry)
                    }
                  >
                    {directory ? (
                      <ChevronRight
                        size={14}
                        className="shrink-0 text-[var(--theme-text-tertiary)]"
                        aria-hidden="true"
                      />
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
                      onClick={() =>
                        void onAddToConversation({
                          source_id: sourceId,
                          path: entry.path,
                        })
                      }
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
    </section>
  );
}
