export type TerminalRunStatus = "failed" | "cancelled" | "succeeded";

export interface RunStatusWire {
  status?: string | null;
  raw_status?: string | null;
}

const TERMINAL_STATUS_ALIASES: Record<string, TerminalRunStatus> = {
  cancelled: "cancelled",
  complete: "succeeded",
  completed: "succeeded",
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

/** Prefer the platform status when the compatibility projection supplies it. */
export function authoritativeRunStatus(
  status: RunStatusWire,
): string | null {
  const rawStatus = normalizeRunStatus(status.raw_status);
  return rawStatus ?? normalizeRunStatus(status.status);
}

function normalizeRunStatus(value: unknown): string | null {
  return typeof value === "string" && value.trim()
    ? value.trim().toLowerCase()
    : null;
}

/** Return a terminal outcome only when the application frame explicitly carries one. */
export function terminalRunStatusFromEvent(
  eventType: string,
  data: Record<string, unknown>,
): TerminalRunStatus | null {
  const explicitStatus =
    terminalRunStatus(data.event_type) ??
    terminalRunStatus(data.status) ??
    terminalRunStatus(data.error);
  if (explicitStatus) {
    return explicitStatus;
  }

  // `error` is an SSE envelope, not a terminal state: lambchat also uses it
  // for stream_timeout while the run remains authoritative-running. Those
  // frames must reconcile through status instead of inventing failure.
  return eventType === "error" ? null : terminalRunStatus(eventType);
}

/** Only these states may reconnect an SSE stream or retain an active run. */
export function isActiveRunStatus(value: unknown): boolean {
  if (typeof value !== "string") {
    return false;
  }
  return [
    "pending",
    "queued",
    "running",
    "processing",
    "cancel_requested",
  ].includes(
    value.trim().toLowerCase(),
  );
}
