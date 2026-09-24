import {
  Eye,
  EyeOff,
  FileText,
  FolderOpen,
  KeyRound,
  LockKeyhole,
  Plug,
  Search,
  SearchX,
  Server,
  ShieldCheck,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { createPortal } from "react-dom";

import { PanelHeader } from "../common/PanelHeader";
import { profileDriveApi, ProfileDriveRequestError } from "../../services/api/profileDrive";
import { workbenchSurface } from "../workbench/workbenchSurface";

const plugin = {
  id: "personal-file-server",
  name: "本地文件服务器",
  description: "连接你的企业个人目录，让 AI 在授权范围内查找和读取 Desktop 文件。",
  provider: "企业 IT",
  category: "企业资源",
  capabilities: [
    { label: "列出文件", icon: FolderOpen },
    { label: "搜索文件", icon: Search },
    { label: "读取文本", icon: FileText },
  ],
} as const;

function profileDriveErrorMessage(error: unknown): string {
  if (error instanceof ProfileDriveRequestError) {
    if (error.code === "credential_rejected") {
      return "企业账号或密码不正确。";
    }
    if (error.code === "profile_drive_not_configured") {
      return "当前用户尚未配置本地文件服务器。";
    }
    if (error.code === "drive_unavailable") {
      return "文件服务器暂不可用，请稍后重试。";
    }
  }
  return "认证失败，请稍后重试。";
}

function AuthenticationDialog({
  open,
  onClose,
  onAuthenticated,
}: {
  open: boolean;
  onClose: () => void;
  onAuthenticated: () => void;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const resetAndClose = useCallback(() => {
    setPassword("");
    setShowPassword(false);
    setError(null);
    setIsSubmitting(false);
    onClose();
  }, [onClose]);

  useEffect(() => {
    if (!open) return undefined;

    restoreFocusRef.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    document.body.style.overflow = "hidden";
    queueMicrotask(() => passwordRef.current?.focus());

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        resetAndClose();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;

      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable.at(-1)!;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = "";
      restoreFocusRef.current?.focus();
      restoreFocusRef.current = null;
    };
  }, [open, resetAndClose]);

  if (!open) return null;

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!password) {
      setError("请输入企业账号密码。");
      return;
    }

    setIsSubmitting(true);
    setError(null);
    try {
      const result = await profileDriveApi.connect(password);
      if (!result.connected) {
        setError("认证未建立文件服务器连接。");
        return;
      }
      onAuthenticated();
      resetAndClose();
    } catch (submissionError) {
      setError(profileDriveErrorMessage(submissionError));
    } finally {
      setIsSubmitting(false);
    }
  };

  return createPortal(
    <div className="fixed inset-0 z-[300] flex items-center justify-center p-4">
      <button
        type="button"
        aria-label="关闭本地文件服务器认证"
        className="absolute inset-0 cursor-default bg-[var(--theme-overlay-strong)]"
        onClick={resetAndClose}
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="file-server-auth-title"
        className="relative z-10 w-full max-w-md overflow-hidden rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] shadow-[0_18px_48px_rgba(15,23,42,0.24)]"
      >
        <div className="flex items-start gap-3 border-b border-[var(--theme-border)] px-5 py-4">
          <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-cyan-500/10 text-cyan-700 ring-1 ring-cyan-600/20 dark:text-cyan-300">
            <Server aria-hidden="true" size={20} />
          </span>
          <div className="min-w-0 flex-1">
            <h2
              id="file-server-auth-title"
              className="text-base font-semibold text-[var(--theme-text)]"
            >
              认证本地文件服务器
            </h2>
            <p className="mt-1 text-xs leading-5 text-[var(--theme-text-secondary)]">
              使用当前登录账号建立个人 Desktop 的受控连接。
            </p>
          </div>
        </div>

        <form autoComplete="off" onSubmit={handleSubmit}>
          <div className="space-y-4 px-5 py-5">
            <div>
              <label
                htmlFor="file-server-password"
                className="mb-1.5 block text-sm font-medium text-[var(--theme-text)]"
              >
                密码
              </label>
              <div className="relative">
                <LockKeyhole
                  aria-hidden="true"
                  className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--theme-text-tertiary)]"
                  size={17}
                />
                <input
                  ref={passwordRef}
                  id="file-server-password"
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(event) => {
                    setPassword(event.target.value);
                    setError(null);
                  }}
                  className="h-11 w-full rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-canvas)] pl-10 pr-11 text-sm text-[var(--theme-text)] outline-none transition-colors placeholder:text-[var(--theme-text-tertiary)] focus:border-[var(--theme-primary)] focus:ring-2 focus:ring-[var(--theme-primary)]/15"
                  placeholder="输入域账号密码"
                />
                <button
                  type="button"
                  aria-label={showPassword ? "隐藏密码" : "显示密码"}
                  title={showPassword ? "隐藏密码" : "显示密码"}
                  onClick={() => setShowPassword((visible) => !visible)}
                  className="absolute right-1.5 top-1/2 flex size-8 -translate-y-1/2 items-center justify-center rounded-md text-[var(--theme-text-secondary)] hover:bg-[var(--theme-hover)] hover:text-[var(--theme-text)]"
                >
                  {showPassword ? (
                    <EyeOff aria-hidden="true" size={17} />
                  ) : (
                    <Eye aria-hidden="true" size={17} />
                  )}
                </button>
              </div>
            </div>

            {error ? (
              <p
                role="alert"
                className="rounded-md bg-[var(--theme-danger-soft)] px-3 py-2 text-xs text-[var(--theme-danger)] ring-1 ring-[var(--theme-danger-ring)]"
              >
                {error}
              </p>
            ) : null}

            <div className="flex items-start gap-2 text-xs leading-5 text-[var(--theme-text-secondary)]">
              <ShieldCheck
                aria-hidden="true"
                className="mt-0.5 shrink-0 text-emerald-600 dark:text-emerald-400"
                size={16}
              />
              <span>密码只用于建立当前会话，不会保存，也不会提供给 AI。</span>
            </div>
          </div>

          <div className="flex justify-end gap-2 border-t border-[var(--theme-border)] bg-[var(--theme-workbench-canvas)] px-5 py-3">
            <button
              type="button"
              disabled={isSubmitting}
              className="btn-secondary min-h-10 px-4 text-sm"
              onClick={resetAndClose}
            >
              取消
            </button>
            <button
              type="submit"
              disabled={isSubmitting}
              aria-busy={isSubmitting}
              className="btn-primary inline-flex min-h-10 items-center justify-center gap-2 px-4 text-sm"
            >
              <KeyRound aria-hidden="true" size={16} />
              {isSubmitting ? "认证中..." : "提交认证"}
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  );
}

export function PluginMarketPanel() {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<"all" | "enterprise">("all");
  const [authOpen, setAuthOpen] = useState(false);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    let active = true;
    void profileDriveApi
      .status()
      .then((status) => {
        if (active) setConnected((current) => current || status.connected);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);

  const visiblePlugins = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    const matchesCategory =
      category === "all" || plugin.category === "企业资源";
    const matchesQuery =
      !normalizedQuery ||
      [plugin.name, plugin.description, plugin.provider, plugin.category]
        .join(" ")
        .toLocaleLowerCase()
        .includes(normalizedQuery);
    return matchesCategory && matchesQuery ? [plugin] : [];
  }, [category, query]);

  return (
    <div
      data-plugin-market
      className={workbenchSurface.page}
    >
      <PanelHeader
        title="插件市场"
        subtitle="连接企业数据源与服务，为 AI 工作区增加受控能力。"
        icon={<Plug aria-hidden="true" size={20} />}
        searchValue={query}
        onSearchChange={setQuery}
        searchPlaceholder="搜索插件"
      />

      <div className="border-b border-[var(--theme-border)] px-4 py-3">
        <div
          role="group"
          aria-label="插件分类"
          className="inline-flex rounded-md bg-[var(--theme-bg-sidebar)] p-1 ring-1 ring-[var(--theme-border)]"
        >
          {([
            ["all", "全部"],
            ["enterprise", "企业资源"],
          ] as const).map(([value, label]) => (
            <button
              key={value}
              type="button"
              aria-pressed={category === value}
              onClick={() => setCategory(value)}
              className={`min-h-8 rounded px-3 text-xs font-medium transition-colors ${
                category === value
                  ? "bg-[var(--theme-workbench-panel)] text-[var(--theme-text)] shadow-sm"
                  : "text-[var(--theme-text-secondary)] hover:text-[var(--theme-text)]"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className={workbenchSurface.catalog.content}>
        <div className="mb-3 flex items-center justify-between gap-3">
          <p className="text-xs text-[var(--theme-text-secondary)]">
            {visiblePlugins.length} 个可用插件
          </p>
        </div>

        {visiblePlugins.length > 0 ? (
          <div className={workbenchSurface.catalog.cardGrid}>
            <article
              data-plugin-card={plugin.id}
              className={`${workbenchSurface.catalog.entryCard} flex min-h-64 min-w-0 flex-col`}
            >
              <div className="flex items-start gap-3">
                <span className="flex size-11 shrink-0 items-center justify-center rounded-lg bg-cyan-500/10 text-cyan-700 ring-1 ring-cyan-600/20 dark:text-cyan-300">
                  <Server aria-hidden="true" size={22} />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                      <h2 className="text-sm font-semibold text-[var(--theme-text)]">
                        {plugin.name}
                      </h2>
                      <p className="mt-0.5 text-xs text-[var(--theme-text-secondary)]">
                        {plugin.provider} · MCP 连接
                      </p>
                    </div>
                    <span
                      className={`rounded-md px-2 py-1 text-[11px] font-medium ring-1 ${
                        connected
                          ? "bg-emerald-500/10 text-emerald-800 ring-emerald-600/20 dark:text-emerald-200"
                          : "bg-amber-500/10 text-amber-800 ring-amber-600/20 dark:text-amber-200"
                      }`}
                    >
                      {connected ? "已连接" : "未连接"}
                    </span>
                  </div>
                </div>
              </div>

              <p className="mt-4 text-sm leading-6 text-[var(--theme-text-secondary)]">
                {plugin.description}
              </p>

              <div className="mt-4 flex flex-wrap gap-2">
                {plugin.capabilities.map(({ label, icon: CapabilityIcon }) => (
                  <span
                    key={label}
                    className="inline-flex items-center gap-1.5 rounded-md bg-[var(--theme-bg-sidebar)] px-2.5 py-1.5 text-xs text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]"
                  >
                    <CapabilityIcon aria-hidden="true" size={14} />
                    {label}
                  </span>
                ))}
              </div>

              <div className="mt-auto flex items-center justify-between gap-3 border-t border-[var(--theme-border)] pt-4">
                <span className="inline-flex items-center gap-1.5 text-xs text-[var(--theme-text-secondary)]">
                  <ShieldCheck
                    aria-hidden="true"
                    className="text-emerald-600 dark:text-emerald-400"
                    size={15}
                  />
                  用户级受控访问
                </span>
                <button
                  type="button"
                  onClick={() => setAuthOpen(true)}
                  className="btn-primary inline-flex min-h-10 items-center justify-center gap-2 px-4 text-sm"
                >
                  <KeyRound aria-hidden="true" size={16} />
                  认证
                </button>
              </div>
            </article>
          </div>
        ) : (
          <div className={workbenchSurface.catalog.emptyState}>
            <span className={workbenchSurface.catalog.emptyIcon}>
              <SearchX aria-hidden="true" size={21} />
            </span>
            <h2 className={workbenchSurface.catalog.emptyTitle}>未找到插件</h2>
            <p className={workbenchSurface.catalog.emptyDescription}>
              尝试更换搜索词或清除筛选条件。
            </p>
            <button
              type="button"
              className="btn-secondary mt-4 min-h-10 px-4 text-sm"
              onClick={() => {
                setQuery("");
                setCategory("all");
              }}
            >
              清除筛选
            </button>
          </div>
        )}
      </div>

      <AuthenticationDialog
        open={authOpen}
        onClose={() => setAuthOpen(false)}
        onAuthenticated={() => setConnected(true)}
      />
    </div>
  );
}
