import { Button } from "@lobehub/ui/base-ui";
import type { ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { AlertCircle, ArrowLeft, type LucideIcon } from "lucide-react";
import { APP_ROUTE_PATHS } from "../../appRouteManifest";

export function AppFrame({
  title,
  subtitle,
  icon: Icon,
  status,
  children,
}: {
  title: string;
  subtitle: string;
  icon: LucideIcon;
  status?: ReactNode;
  children: ReactNode;
}) {
  const navigate = useNavigate();
  return (
    <main className="flex h-full min-h-0 flex-col overflow-hidden bg-[var(--theme-workbench-canvas)] text-[var(--theme-text)]">
      <header className="shrink-0 border-b border-[var(--theme-border)] bg-[var(--theme-workbench-panel)]">
        <div className="mx-auto flex min-h-[72px] w-full max-w-[1440px] items-center gap-4 px-5 sm:px-8">
          <Button type="text" size="large"
            htmlType="button"
            onClick={() => navigate(APP_ROUTE_PATHS.apps)}
            icon={<ArrowLeft size={19} />}
            className="shrink-0"
            aria-label="返回 AI 应用"
            title="返回 AI 应用"
          />
          <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-[var(--theme-bg-sidebar)] text-[var(--theme-primary)]">
            <Icon size={21} strokeWidth={1.9} />
          </span>
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-lg font-bold text-[var(--theme-text)]">{title}</h1>
            <p className="truncate text-xs text-[var(--theme-text-secondary)]">{subtitle}</p>
          </div>
          {status}
        </div>
      </header>
      <div data-ai-application-scroller className="mx-auto min-h-0 w-full max-w-[1440px] flex-1 overflow-y-auto px-5 py-5 sm:px-8 sm:py-7">{children}</div>
    </main>
  );
}

export function ConnectionNotice({ failed, message }: { failed: boolean; message?: string }) {
  if (!failed) return null;
  return (
    <div className="connection-notice flex items-start gap-2 rounded-lg border border-[var(--theme-warning-ring)] bg-[var(--theme-warning-soft)] px-4 py-3 text-sm text-[var(--theme-warning)]">
      <AlertCircle size={17} className="mt-0.5 shrink-0" />
      <span>{message || "应用配置暂时无法连接，页面仍可预览；连接平台后即可提交任务。"}</span>
    </div>
  );
}

export function AgentStatus({ loading, failed, label = "平台已接入" }: { loading: boolean; failed: boolean; label?: string }) {
  const text = loading ? "正在连接" : failed ? "待连接" : label;
  const color = loading ? "text-[var(--theme-warning)]" : failed ? "text-[var(--theme-warning)]" : "text-[var(--theme-success)]";
  return (
    <span className={`hidden items-center gap-1.5 text-xs font-medium sm:flex ${color}`}>
      <span className={`size-2 rounded-full ${loading ? "bg-[var(--theme-warning)]" : failed ? "bg-[var(--theme-warning)]" : "bg-[var(--theme-success)]"}`} />
      {text}
    </span>
  );
}
