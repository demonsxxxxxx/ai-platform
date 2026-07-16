/** Chinese fallbacks for structured product notifications with missing locale fields. */
export const CHINESE_NOTIFICATION_TITLE_FALLBACK = "通知内容暂不可用";
export const CHINESE_NOTIFICATION_CONTENT_FALLBACK = "";

/**
 * Returns only the Chinese projection of a structured product notification.
 * User and external content must remain outside this projection helper.
 */
export function resolveChineseNotificationText(
  value: object | null | undefined,
  fallback = CHINESE_NOTIFICATION_TITLE_FALLBACK,
): string {
  const chinese =
    value &&
    "zh" in value &&
    typeof value.zh === "string"
      ? value.zh.trim()
      : "";
  return chinese || fallback;
}
