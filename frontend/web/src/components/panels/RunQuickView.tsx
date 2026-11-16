import { Button, DrawerRoot, DrawerPortal, DrawerBackdrop, DrawerPopup, DrawerHeader, DrawerTitle, DrawerContent } from "@lobehub/ui/base-ui";
import { X } from "lucide-react";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useRunSection } from "./useRunDetail";
import { adminRunsApi } from "../../services/api/adminRuns";
import { runDetailUrl } from "./runDetailNavigation";
import { runDisplayTitle, dateTime } from "./runPresentation";
import { StatusBadge, RuntimeHealth } from "./RunDetailFacts";
import { PanelLoadingState } from "../common/PanelLoadingState";

const readDetail = (id: string) => adminRunsApi.detail(id);
const readDiagnostics = (id: string) => adminRunsApi.diagnostics(id);

export function RunQuickView({ runId, search, onClose }: { runId: string; search: string; onClose: () => void }) {
  const [open, setOpen] = useState(true);
  const returnFocus = useRef(document.activeElement instanceof HTMLElement ? document.activeElement : null);
  const detail = useRunSection(runId, readDetail);
  const diagnostics = useRunSection(runId, readDiagnostics);
  return <DrawerRoot open={open} onOpenChange={setOpen} onExitComplete={() => { onClose(); window.requestAnimationFrame(() => { if (window.location.pathname === "/runs") (returnFocus.current?.isConnected ? returnFocus.current : document.querySelector<HTMLButtonElement>('button[aria-label="刷新最近运行"]'))?.focus(); }); }}><DrawerPortal><DrawerBackdrop data-run-monitor-backdrop /><DrawerPopup finalFocus={returnFocus} placement="right" width="min(32rem, 100vw)" data-run-quick-view aria-label="运行快速查看">
    <DrawerHeader><DrawerTitle>{detail.data ? runDisplayTitle(detail.data.run) : "运行快速查看"}</DrawerTitle><Button type="text" autoFocus aria-label="关闭运行详情" icon={<X size={18} />} onClick={() => setOpen(false)} /></DrawerHeader>
    <DrawerContent><div className="space-y-4 p-4 text-sm">
      {!detail.data && detail.loading ? <PanelLoadingState text="正在读取运行摘要" /> : null}
      {detail.error ? <p role="alert">{detail.error}</p> : null}
      {detail.data ? <><StatusBadge status={detail.data.run.status} /><p>{dateTime(detail.data.run.started_at)}</p><RuntimeHealth detail={detail.data} />
        {diagnostics.data?.root ? <p className="text-[var(--theme-danger-text)]">{diagnostics.data.root.stage} · {diagnostics.data.root.message || diagnostics.data.root.error_code}</p> : null}
        <p>{detail.data.worker_execution.response ? "已有公开回答" : "未记录公开回答"} · {detail.data.artifacts.length} 个已登记产物</p>
        <p className="text-xs text-[var(--theme-text-secondary)]">登记不代表文件已核验可访问；完整证据在独立详情页查看。</p>
      </> : null}
      <Link className="font-medium text-[var(--theme-primary)] underline" to={runDetailUrl(runId, search)} onClick={onClose}>打开完整运行详情</Link>
    </div></DrawerContent>
  </DrawerPopup></DrawerPortal></DrawerRoot>;
}
