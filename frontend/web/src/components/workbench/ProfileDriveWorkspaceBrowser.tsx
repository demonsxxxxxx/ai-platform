import {
  ArrowLeft,
  Eye,
  FileText,
  Folder,
  RefreshCw,
  Server,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import toast from "react-hot-toast";

import type { SessionInputFile } from "../../services/api";
import {
  profileDriveApi,
  ProfileDriveRequestError,
  type ProfileDriveFileEntry,
} from "../../services/api/profileDrive";
import { sessionApi } from "../../services/api/session";
import { formatFileSize } from "../documents/utils";
import { workbenchSurface } from "./workbenchSurface";

interface ProfileDriveWorkspaceBrowserProps {
  sessionId: string | null;
  onImported: (file: SessionInputFile) => void;
}

function parentPath(path: string): string {
  const separator = path.lastIndexOf("/");
  return separator < 0 ? "" : path.slice(0, separator);
}

function browserError(error: unknown): string {
  if (
    error instanceof ProfileDriveRequestError &&
    ["reauth_required", "credential_rejected"].includes(error.code)
  ) {
    return "个人文件服务器需要重新认证。";
  }
  return "个人文件暂时不可用。";
}

export function ProfileDriveWorkspaceBrowser({
  sessionId,
  onImported,
}: ProfileDriveWorkspaceBrowserProps) {
  const [connected, setConnected] = useState<boolean | null>(null);
  const [path, setPath] = useState("");
  const [entries, setEntries] = useState<ProfileDriveFileEntry[]>([]);
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
      const result = await profileDriveApi.listFiles(nextPath);
      if (requestId !== requestIdRef.current) return;
      setPath(result.path);
      setEntries(result.entries);
      setTruncated(result.truncated);
      setConnected(true);
    } catch (loadError) {
      if (requestId !== requestIdRef.current) return;
      setEntries([]);
      setTruncated(false);
      setError(browserError(loadError));
    } finally {
      if (requestId === requestIdRef.current) setLoading(false);
    }
  }, []);

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
        setError(browserError(statusError));
        setLoading(false);
      });
    return () => {
      active = false;
      mountedRef.current = false;
      requestIdRef.current += 1;
    };
  }, [load]);

  const preview = useCallback(
    async (entry: ProfileDriveFileEntry) => {
      if (!sessionId || importingPath) return;
      setImportingPath(entry.path);
      try {
        const imported = await sessionApi.importProfileDriveFile(
          sessionId,
          entry.path,
        );
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
    [importingPath, onImported, sessionId],
  );

  return (
    <section
      data-librechat-context-section="profile-drive"
      aria-labelledby="librechat-profile-drive-label"
      className={`${workbenchSurface.compactPanel} mt-3 p-3`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className={workbenchSurface.catalog.compactIconBox}>
            <Server size={15} aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <h3
              id="librechat-profile-drive-label"
              className="truncate text-xs font-semibold text-[var(--theme-text)]"
            >
              个人文件
            </h3>
            {path && (
              <p
                className="truncate text-[10px] text-[var(--theme-text-tertiary)]"
                title={path}
              >
                {path}
              </p>
            )}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {path && (
            <button
              type="button"
              className="rounded p-1 text-[var(--theme-text-tertiary)] hover:bg-[var(--theme-workbench-panel)] hover:text-[var(--theme-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-primary)]"
              aria-label="返回上级目录"
              title="返回上级目录"
              onClick={() => void load(parentPath(path))}
            >
              <ArrowLeft size={14} />
            </button>
          )}
          <button
            type="button"
            className="rounded p-1 text-[var(--theme-text-tertiary)] hover:bg-[var(--theme-workbench-panel)] hover:text-[var(--theme-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-primary)] disabled:opacity-50"
            aria-label="刷新个人文件"
            title="刷新个人文件"
            disabled={loading || connected === false}
            onClick={() => void load(path)}
          >
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
          </button>
          <span className={workbenchSurface.catalog.chip}>
            {loading ? "…" : connected ? entries.length : "!"}
          </span>
        </div>
      </div>

      <div className="mt-3">
        {error ? (
          <p role="status" className="text-xs leading-5 text-[var(--theme-danger)]">
            {error}
          </p>
        ) : connected === false ? (
          <p className={workbenchSurface.mutedText}>个人文件服务器未连接。</p>
        ) : loading ? (
          <p className={workbenchSurface.mutedText}>正在加载个人文件…</p>
        ) : entries.length === 0 ? (
          <p className={workbenchSurface.mutedText}>此目录为空。</p>
        ) : (
          <div className="space-y-1.5">
            {entries.map((entry) => {
              const directory = entry.type === "directory";
              const importing = importingPath === entry.path;
              const disabled = Boolean(importingPath) || (!directory && !sessionId);
              return (
                <div
                  key={`${entry.type}:${entry.path}`}
                  className="flex min-h-10 items-center gap-2 rounded-md bg-[var(--theme-bg-sidebar)] px-2 py-1.5 ring-1 ring-[var(--theme-border)]"
                >
                  {directory ? (
                    <Folder
                      size={15}
                      className="shrink-0 text-amber-600 dark:text-amber-300"
                      aria-hidden="true"
                    />
                  ) : (
                    <FileText
                      size={15}
                      className="shrink-0 text-[var(--theme-text-tertiary)]"
                      aria-hidden="true"
                    />
                  )}
                  <button
                    type="button"
                    className="min-w-0 flex-1 text-left disabled:cursor-default"
                    title={entry.name}
                    disabled={disabled}
                    onClick={() =>
                      directory ? void load(entry.path) : void preview(entry)
                    }
                  >
                    <span className="block truncate text-xs font-medium text-[var(--theme-text)]">
                      {entry.name}
                    </span>
                    {!directory && entry.size !== null && (
                      <span className="block text-[11px] text-[var(--theme-text-tertiary)]">
                        {formatFileSize(entry.size)}
                      </span>
                    )}
                  </button>
                  {!directory && (
                    <Eye
                      size={14}
                      aria-label={importing ? "正在导入" : "预览文件"}
                      className={`shrink-0 text-[var(--theme-text-tertiary)] ${
                        importing ? "animate-pulse" : ""
                      }`}
                    />
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
