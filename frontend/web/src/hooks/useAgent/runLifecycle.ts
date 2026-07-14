export type TerminalRunStatus = "failed" | "cancelled" | "succeeded";

const TERMINAL_STATUS_ALIASES: Record<string, TerminalRunStatus> = {
  cancelled: "cancelled",
  complete: "succeeded",
  completed: "succeeded",
  error: "failed",
  failed: "failed",
  run_cancelled: "cancelled",
  run_completed: "succeeded",
  run_failed: "failed",
  run_succeeded: "succeeded",
  succeeded: "succeeded",
};

/** Convert public run-event vocabulary into one terminal lifecycle outcome. */
export function terminalRunStatus(value: unknown): TerminalRunStatus | null {
  if (typeof value !== "string") {
    return null;
  }
  return TERMINAL_STATUS_ALIASES[value.trim().toLowerCase()] || null;
}

/** Return a terminal outcome only when the event explicitly carries one. */
export function terminalRunStatusFromEvent(
  eventType: string,
  data: Record<string, unknown>,
): TerminalRunStatus | null {
  return (
    terminalRunStatus(data.event_type) ??
    terminalRunStatus(data.status) ??
    (eventType === "error" ? null : terminalRunStatus(eventType))
  );
}

/** Only these states may reconnect an SSE stream or retain an active run. */
export function isActiveRunStatus(value: unknown): boolean {
  if (typeof value !== "string") {
    return false;
  }
  return ["pending", "queued", "running", "processing"].includes(
    value.trim().toLowerCase(),
  );
}
