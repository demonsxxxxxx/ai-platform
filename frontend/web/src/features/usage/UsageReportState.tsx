import { Button } from "@lobehub/ui/base-ui";
import type { UsageMeta } from "../../services/api/modelUsage";
import { ApiRequestError } from "../../services/api/fetch";

export function UsageError({ error, retry }: { error: unknown; retry: () => void }) {
  const status = error instanceof ApiRequestError ? error.status : undefined;
  return <div role="alert" className="flex flex-wrap items-center gap-3 py-3 text-sm">
    <span>{status === 401 ? "登录已失效，请重新登录后查看用量。" : status === 403 ? "无权查看用量统计，仅管理员可访问。" : "用量加载失败，请重试。"}</span>
    {status !== 401 && status !== 403 && <Button onClick={retry}>重试</Button>}
  </div>;
}

const reasons = {
  not_collected: "所选日期包含尚未采集的历史区间。",
  report_missing: "部分用量报告缺失。",
  baseline_unknown: "部分用量缺少可信基线。",
  partial_report: "部分报告未完整上报。",
};

export function UsageCoverage({ meta }: { meta: UsageMeta }) {
  const { coverage } = meta;
  return <div role="status" className="space-y-1 text-sm text-[var(--theme-text-secondary)]">
    <p className="font-medium text-[var(--theme-text)]">{coverage.status === "reported_final" ? "已收到最终报告" : coverage.status === "partial" ? "采集不完整 · 仅展示已记录用量" : "用量不可完整统计 · 未采集不代表零用量"}</p>
    {coverage.reason && <p>{reasons[coverage.reason]}</p>}
    <p>统计窗口：{meta.start_date} 至 {meta.end_date}（北京时间）。报告范围 {coverage.scopes}，不完整范围 {coverage.partial_scopes}。</p>
    {meta.collection_started_at && <p>采集启用：{new Date(meta.collection_started_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false })}（北京时间）</p>}
  </div>;
}

export function UsageEmpty({ meta }: { meta: UsageMeta }) {
  return <span>{meta.coverage.status === "reported_final" ? "所选范围没有已记录用量" : "暂无可展示的用量记录；未采集不代表零用量"}</span>;
}
