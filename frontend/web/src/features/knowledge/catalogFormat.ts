export function safeDate(value: string | null): string {
  if (!value) return "尚未完成";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf())
    ? "尚未完成"
    : parsed.toLocaleString("zh-CN");
}
