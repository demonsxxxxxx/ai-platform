import type { HistoryEvent } from "./types";

interface HistoryRunSessionData {
  metadata?: Record<string, unknown> | null;
}

interface HistoryRunEventsData {
  run_id?: string;
  current_run_id?: string | null;
  events?: HistoryEvent[];
}

export interface ResolveHistoryCurrentRunIdInput {
  targetRunId?: string | null;
  previousRunId?: string | null;
  sessionData?: HistoryRunSessionData | null;
  eventsData?: HistoryRunEventsData | null;
}

export interface HistoryLoadTokenRef {
  current: number;
}

export function beginHistoryLoad(historyLoadTokenRef: HistoryLoadTokenRef): number {
  historyLoadTokenRef.current += 1;
  return historyLoadTokenRef.current;
}

export function isCurrentHistoryLoad(
  historyLoadTokenRef: HistoryLoadTokenRef,
  historyLoadToken: number,
): boolean {
  return historyLoadTokenRef.current === historyLoadToken;
}

export function resolveHistoryCurrentRunId({
  targetRunId,
  sessionData,
  eventsData,
}: ResolveHistoryCurrentRunIdInput): string | null {
  return (
    normalizeRunId(targetRunId) ??
    normalizeRunId(eventsData?.current_run_id) ??
    normalizeRunId(eventsData?.run_id) ??
    resolveSessionMetadataRunId(sessionData?.metadata) ??
    null
  );
}

function resolveSessionMetadataRunId(
  metadata: Record<string, unknown> | null | undefined,
): string | null {
  if (!metadata) {
    return null;
  }

  return (
    normalizeRunId(metadata.current_run_id) ??
    normalizeRunId(metadata.latest_run_id) ??
    resolveNestedRunId(metadata.current_run) ??
    resolveNestedRunId(metadata.latest_run) ??
    null
  );
}

function resolveNestedRunId(value: unknown): string | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  return normalizeRunId((value as Record<string, unknown>).run_id);
}

function normalizeRunId(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}
