export const RUN_DETAIL_TABS = [
  { key: "overview", label: "概览" },
  { key: "execution", label: "执行过程" },
  { key: "results", label: "结果与产物" },
  { key: "configuration", label: "输入与配置" },
  { key: "history", label: "历史与重试" },
] as const;
export type RunDetailTab = (typeof RUN_DETAIL_TABS)[number]["key"];
const LIST_KEYS = ["hours", "status", "model", "order", "agent_id", "user_id", "page"];
export function runListSearch(search: string): string {
  const current = new URLSearchParams(search);
  const result = new URLSearchParams();
  for (const key of LIST_KEYS) { const value = current.get(key); if (value) result.set(key, value); }
  return result.size ? `?${result}` : "";
}
export function runDetailUrl(runId: string, search = "", tab: RunDetailTab = "overview"): string {
  const params = new URLSearchParams(runListSearch(search));
  if (tab !== "overview") params.set("tab", tab);
  return `/runs/${encodeURIComponent(runId)}${params.size ? `?${params}` : ""}`;
}
export function readRunDetailTab(search: string): RunDetailTab {
  const tab = new URLSearchParams(search).get("tab");
  return RUN_DETAIL_TABS.find((item) => item.key === tab)?.key ?? "overview";
}
