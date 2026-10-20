import type { AdminRunLatency } from "../../services/api/adminRuns";

export const RUN_LATENCY_LABELS: Record<keyof AdminRunLatency, string> = {
  total_ms: "任务总耗时",
  queue_ms: "排队等待",
  execution_ms: "执行与落库",
  server_first_text_ms: "服务端首段文本等待",
  browser_first_text_ms: "浏览器首段正文（DOM）",
};

export function formatRunDuration(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value) || value < 0) return "未采集";
  if (value < 1000) return `${Math.round(value)} ms`;
  if (value < 60000) return `${(value / 1000).toFixed(2)} 秒`;
  return `${(value / 60000).toFixed(1)} 分钟`;
}
