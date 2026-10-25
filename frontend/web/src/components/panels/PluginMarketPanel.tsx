import {
  Button, Input, ModalBackdrop, ModalClose, ModalContent, ModalDescription,
  ModalFooter, ModalHeader, ModalPopup, ModalPortal, ModalRoot, ModalTitle,
} from "@lobehub/ui/base-ui";
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
  type RefObject,
} from "react";

import { PanelHeader } from "../common/PanelHeader";
import {
  PROFILE_DRIVE_CONNECTION_CHANGED_EVENT,
  profileDriveApi,
  ProfileDriveRequestError,
} from "../../services/api/profileDrive";
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
  returnFocusRef,
}: {
  open: boolean;
  onClose: () => void;
  onAuthenticated: () => void;
  returnFocusRef: RefObject<HTMLButtonElement | null>;
}) {
  const passwordRef = useRef<HTMLInputElement>(null);
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
      window.dispatchEvent(new Event(PROFILE_DRIVE_CONNECTION_CHANGED_EVENT));
      resetAndClose();
    } catch (submissionError) {
      setError(profileDriveErrorMessage(submissionError));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <ModalRoot open={open} onOpenChange={(nextOpen) => { if (!nextOpen) resetAndClose(); }}>
      <ModalPortal>
        <ModalBackdrop />
        <ModalPopup width={448} initialFocus={passwordRef} finalFocus={returnFocusRef}>
          <ModalHeader>
            <ModalTitle>认证本地文件服务器</ModalTitle>
            <ModalClose aria-label="关闭本地文件服务器认证" />
          </ModalHeader>
          <form autoComplete="off" onSubmit={handleSubmit}>
            <ModalContent className="space-y-5">
              <ModalDescription className="text-sm leading-6 text-[var(--theme-text-secondary)]">
                使用当前登录账号建立个人 Desktop 的受控连接。
              </ModalDescription>
              <div>
                <label htmlFor="file-server-password" className="mb-2 block text-sm font-medium">
                  密码
                </label>
                <Input
                  ref={passwordRef}
                  id="file-server-password"
                  size="large"
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(event) => {
                    setPassword(event.target.value);
                    setError(null);
                  }}
                  prefix={<LockKeyhole aria-hidden="true" size={18} />}
                  suffix={<Button type="text" htmlType="button"
                    aria-label={showPassword ? "隐藏密码" : "显示密码"}
                    onClick={() => setShowPassword((visible) => !visible)}
                    icon={showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
                  />}
                  aria-invalid={Boolean(error)}
                  aria-describedby={error ? "file-server-password-error" : undefined}
                  autoComplete="off"
                  placeholder="输入域账号密码"
                />
              </div>
              {error ? (
                <p id="file-server-password-error" role="alert" className="text-sm text-[var(--theme-danger-text)]">
                  {error}
                </p>
              ) : null}
              <p className="flex items-start gap-2 text-xs leading-5 text-[var(--theme-text-secondary)]">
                <ShieldCheck aria-hidden="true" className="mt-0.5 shrink-0" size={16} />
                <span>密码只用于建立当前会话，不会保存，也不会提供给 AI。</span>
              </p>
            </ModalContent>
            <ModalFooter>
              <Button size="large" htmlType="button" disabled={isSubmitting} onClick={resetAndClose}>
                取消
              </Button>
              <Button type="primary" size="large" htmlType="submit" disabled={isSubmitting}
                loading={isSubmitting} icon={<KeyRound size={16} />}>
                {isSubmitting ? "认证中..." : "提交认证"}
              </Button>
            </ModalFooter>
          </form>
        </ModalPopup>
      </ModalPortal>
    </ModalRoot>
  );
}

export function PluginMarketPanel() {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<"all" | "enterprise">("all");
  const [authOpen, setAuthOpen] = useState(false);
  const authTriggerRef = useRef<HTMLButtonElement>(null);
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
            <Button type="text"
              key={value}
              htmlType="button"
              aria-pressed={category === value}
              onClick={() => setCategory(value)}
              className={`min-h-8 rounded px-3 text-xs font-medium transition-colors ${
                category === value
                  ? "bg-[var(--theme-workbench-panel)] text-[var(--theme-text)] shadow-sm"
                  : "text-[var(--theme-text-secondary)] hover:text-[var(--theme-text)]"
              }`}
            >
              {label}
            </Button>
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
                <span className="flex size-11 shrink-0 items-center justify-center rounded-lg bg-[var(--theme-info-soft)] text-[var(--theme-info-text)] ring-1 ring-[var(--theme-info-ring)]">
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
                          ? "bg-[var(--theme-success-soft)] text-[var(--theme-success-text)] ring-[var(--theme-success-ring)]"
                          : "bg-[var(--theme-warning-soft)] text-[var(--theme-warning-text)] ring-[var(--theme-warning-ring)]"
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
                    className="text-[var(--theme-success-text)]"
                    size={15}
                  />
                  用户级受控访问
                </span>
                <Button type="primary"
                  ref={authTriggerRef}
                  htmlType="button"
                  onClick={() => setAuthOpen(true)}
                  className="inline-flex min-h-10 items-center justify-center gap-2 px-4 text-sm"
                >
                  <KeyRound aria-hidden="true" size={16} />
                  认证
                </Button>
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
            <Button type="default"
              htmlType="button"
              className="mt-4 min-h-10 px-4 text-sm"
              onClick={() => {
                setQuery("");
                setCategory("all");
              }}
            >
              清除筛选
            </Button>
          </div>
        )}
      </div>

      <AuthenticationDialog
        open={authOpen}
        returnFocusRef={authTriggerRef}
        onClose={() => setAuthOpen(false)}
        onAuthenticated={() => setConnected(true)}
      />
    </div>
  );
}
