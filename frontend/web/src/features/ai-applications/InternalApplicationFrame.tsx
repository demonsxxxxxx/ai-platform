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
    <main className="flex h-full min-h-0 flex-col overflow-hidden bg-[#f5f8fb] text-[#122235]">
      <header className="shrink-0 border-b border-[#dce5ed] bg-white">
        <div className="mx-auto flex min-h-[72px] w-full max-w-[1440px] items-center gap-4 px-5 sm:px-8">
          <Button type="text"
            htmlType="button"
            onClick={() => navigate(APP_ROUTE_PATHS.apps)}
            className="flex size-9 shrink-0 items-center justify-center rounded-lg text-[#5d7389] transition-colors hover:bg-[#eef4f8] hover:text-[#244b6c]"
            aria-label="返回 AI 应用"
            title="返回 AI 应用"
          >
            <ArrowLeft size={19} />
          </Button>
          <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-[#e8f5f5] text-[#188b8d]">
            <Icon size={21} strokeWidth={1.9} />
          </span>
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-lg font-bold text-[#122235]">{title}</h1>
            <p className="truncate text-xs text-[#6d8295]">{subtitle}</p>
          </div>
          {status}
        </div>
      </header>
      <div className="mx-auto min-h-0 w-full max-w-[1440px] flex-1 overflow-y-auto px-5 py-5 sm:px-8 sm:py-7">{children}</div>
    </main>
  );
}

export function ConnectionNotice({ failed, message }: { failed: boolean; message?: string }) {
  if (!failed) return null;
  return (
    <div className="connection-notice flex items-start gap-2 rounded-lg border border-[#f1d6a1] bg-[#fff9eb] px-4 py-3 text-sm text-[#8b651c]">
      <AlertCircle size={17} className="mt-0.5 shrink-0" />
      <span>{message || "应用配置暂时无法连接，页面仍可预览；连接平台后即可提交任务。"}</span>
    </div>
  );
}

export function AgentStatus({ loading, failed, label = "平台已接入" }: { loading: boolean; failed: boolean; label?: string }) {
  const text = loading ? "正在连接" : failed ? "待连接" : label;
  const color = loading ? "text-[#b27619]" : failed ? "text-[#a36a1b]" : "text-[#13876d]";
  return (
    <span className={`hidden items-center gap-1.5 text-xs font-medium sm:flex ${color}`}>
      <span className={`size-2 rounded-full ${loading ? "bg-[#e3a534]" : failed ? "bg-[#e3a534]" : "bg-[#2eb88a]"}`} />
      {text}
    </span>
  );
}
