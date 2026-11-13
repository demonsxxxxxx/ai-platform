/** Compact whole-second display for a completed task's server-measured span. */
export function formatTaskDuration(durationMs: number | undefined): string | null {
  if (typeof durationMs !== "number" || !Number.isFinite(durationMs) || durationMs < 0) return null;
  const totalSeconds = Math.floor(durationMs / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor(totalSeconds / 60) % 60;
  const seconds = totalSeconds % 60;
  return `${hours > 0 ? `${hours}h` : ""}${totalSeconds >= 60 ? `${minutes}m` : ""}${seconds}s`;
}
