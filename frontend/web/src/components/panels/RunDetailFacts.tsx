import { Button } from "@lobehub/ui/base-ui";
import { Copy } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { AdminRunDetailResponse } from "../../services/api/adminRuns";
import { dateTime, STATUS_TONES, statusLabel } from "./runPresentation";

export function StatusBadge({ status }: { status?: string | null }) {
  return <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-2 py-1 text-xs font-medium ring-1 ${STATUS_TONES[status ?? ""] ?? STATUS_TONES.cancelled}`}><span className="size-1.5 rounded-full bg-current" aria-hidden="true" />{statusLabel(status)}</span>;
}
export function CopyRunValue({ label, value }: { label: string; value?: string | null }) {
  const [feedback, setFeedback] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  return <span className="inline-flex items-center gap-2"><Button type="text" htmlType="button" size="small" icon={<Copy size={14} />} disabled={!value} aria-label={`复制${label}`} onClick={() => {
    if (!navigator.clipboard || !value) { setFeedback("无法复制，请手动选择内容"); return; }
    void navigator.clipboard.writeText(value).then(() => setFeedback("已复制"), () => setFeedback("复制失败，请手动选择内容"));
    clearTimeout(timer.current); timer.current = setTimeout(() => setFeedback(""), 2500);
  }}>{label}</Button><span role="status" className="text-xs">{feedback}</span></span>;
}
export function IdentityField({ label, value }: { label: string; value?: string | null }) {
  return <div className="min-w-0"><dt className="flex items-center justify-between gap-2 text-xs text-[var(--theme-text-secondary)]"><span>{label}</span>{value ? <CopyRunValue label={label} value={value} /> : null}</dt><dd className="mt-1 break-all font-mono text-sm">{value || "未记录"}</dd></div>;
}
export function RuntimeHealth({ detail }: { detail: AdminRunDetailResponse }) {
  const health = detail.runtime_health;
  const state = detail.run.status === "queued" ? "queued" : ["succeeded", "failed", "cancelled"].includes(detail.run.status) ? "terminal" : health?.state ?? "unknown";
  const labels = { queued: "尚未开始执行", terminal: "运行已结束", healthy: `${health?.heartbeat_source === "executor" ? "Executor" : "Sandbox"} 心跳正常`, stale: "执行心跳或运行租约已过期；不等于 Run 已失败", unknown: "未知（缺少当前执行证据）", awaiting_reconciliation: "执行器终态已留存，等待平台收尾" };
  return <section className="rounded-md bg-[var(--theme-bg-sidebar)] p-3 text-sm" data-run-runtime-health><h3 className="font-semibold">执行健康（本次观测）</h3><p className="mt-1">{labels[state]}</p>
    {health?.async_dispatch_accepted && detail.run.status === "running" ? <p className="mt-1 text-xs text-[var(--theme-text-secondary)]">异步派发已接纳；队列 Worker 心跳仅记录交接前的队列持有状态。</p> : null}
    {health?.queue_last_heartbeat_at ? <p className="mt-1 text-xs text-[var(--theme-text-secondary)]">队列 Worker 心跳（历史）：{dateTime(health.queue_last_heartbeat_at)} · 队列租约（历史）：{dateTime(health.queue_lease_expires_at)}</p> : null}
    {health?.heartbeat_at && ["healthy", "stale"].includes(state) ? <p className="mt-1 text-xs">{health.heartbeat_source === "executor" ? "Executor" : "Sandbox"} 最近心跳：{dateTime(health.heartbeat_at)}</p> : null}
    {health?.observed_at ? <p className="mt-1 text-xs text-[var(--theme-text-secondary)]">观测时间：{dateTime(health.observed_at)}</p> : null}
  </section>;
}
