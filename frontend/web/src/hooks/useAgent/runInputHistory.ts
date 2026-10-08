import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { sessionApi, type RunInputsProjection } from "../../services/api/session";

interface HistoryState {
  owner: string | null;
  runs: RunInputsProjection[];
  isLoading: boolean;
  loadFailed: boolean;
  hasMore: boolean;
  beforeRunId: string | null;
}

function emptyState(owner: string | null): HistoryState {
  return { owner, runs: [], isLoading: owner !== null, loadFailed: false, hasMore: false, beforeRunId: null };
}

/** Session-owned, read-only history; request generations also bind auth identity. */
export function useRunInputHistory({
  sessionId,
  identityKey,
  runId,
  isRunActive,
  projection,
}: {
  sessionId: string | null;
  identityKey: string;
  runId: string | null;
  isRunActive: boolean;
  projection: RunInputsProjection | null;
}) {
  const owner = sessionId ? JSON.stringify([sessionId, identityKey]) : null;
  const ownerRef = useRef(owner);
  const sequenceRef = useRef(0);
  const controllerRef = useRef<AbortController | null>(null);
  const [state, setState] = useState<HistoryState>(() => emptyState(owner));

  useLayoutEffect(() => {
    if (ownerRef.current === owner) return;
    ownerRef.current = owner;
    sequenceRef.current += 1;
    controllerRef.current?.abort();
    controllerRef.current = null;
    setState(emptyState(owner));
  }, [owner]);

  const retire = useCallback(() => {
    ownerRef.current = null;
    sequenceRef.current += 1;
    controllerRef.current?.abort();
    controllerRef.current = null;
    setState(emptyState(null));
  }, []);

  useEffect(() => () => {
    sequenceRef.current += 1;
    controllerRef.current?.abort();
  }, []);

  const readPage = useCallback(async (beforeRunId?: string) => {
    if (!sessionId || !owner || ownerRef.current !== owner) return false;
    // Refresh supersedes an older page request; concurrent pagination cannot skip a page.
    if (beforeRunId && controllerRef.current) return false;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    const sequence = ++sequenceRef.current;
    const isCurrent = () => ownerRef.current === owner && sequenceRef.current === sequence && !controller.signal.aborted;
    setState((previous) => previous.owner === owner ? { ...previous, isLoading: true } : previous);
    try {
      const page = await sessionApi.getRunInputHistory(sessionId, { beforeRunId, signal: controller.signal });
      if (!isCurrent()) return false;
      setState((previous) => {
        if (previous.owner !== owner) return previous;
        const pageIds = new Set(page.runs.map((run) => run.run_id));
        const cursorIndex = beforeRunId ? previous.runs.findIndex((run) => run.run_id === beforeRunId) : -1;
        const runs = beforeRunId && cursorIndex >= 0
          ? [...previous.runs.slice(0, cursorIndex + 1).filter((run) => !pageIds.has(run.run_id)),
            ...page.runs, ...previous.runs.slice(cursorIndex + 1).filter((run) => !pageIds.has(run.run_id))]
          : beforeRunId ? [...previous.runs.filter((run) => !pageIds.has(run.run_id)), ...page.runs]
          : [...page.runs, ...previous.runs.filter((run) => !pageIds.has(run.run_id))];
        // A fresh first page may be disjoint from cached older pages (including
        // when only a live projection overlaps). Traverse again from its cursor
        // to fill the gap, retaining cached rows and deduplicating each page.
        return {
          owner, runs, isLoading: false, loadFailed: false,
          hasMore: page.has_more,
          beforeRunId: page.next_before_run_id,
        };
      });
      return true;
    } catch {
      if (!isCurrent()) return false;
      setState((previous) => previous.owner === owner ? { ...previous, isLoading: false, loadFailed: true } : previous);
      return false;
    } finally {
      if (controllerRef.current === controller) controllerRef.current = null;
    }
  }, [owner, sessionId]);

  useEffect(() => {
    void readPage();
  }, [readPage, runId, isRunActive]);

  useEffect(() => {
    if (!projection || projection.run_id !== runId || ownerRef.current !== owner) return;
    setState((previous) => {
      if (previous.owner !== owner) return previous;
      const found = previous.runs.some((run) => run.run_id === projection.run_id);
      return {
        ...previous,
        runs: found
          ? previous.runs.map((run) => run.run_id === projection.run_id ? projection : run)
          : [projection, ...previous.runs],
      };
    });
  }, [owner, projection, runId]);

  const loadMore = useCallback(() => {
    if (state.owner !== owner || !state.hasMore || !state.beforeRunId) return Promise.resolve(false);
    return readPage(state.beforeRunId);
  }, [owner, readPage, state.beforeRunId, state.hasMore, state.owner]);
  const refresh = useCallback(() => readPage(), [readPage]);

  return useMemo(() => {
    const visible = state.owner === owner ? state : emptyState(owner);
    // Prefer the current poll over the independently fetched history page.
    const runs = isRunActive && projection && projection.run_id === runId
      ? [projection, ...visible.runs.filter((run) => run.run_id !== projection.run_id)]
      : visible.runs;
    return {
      runs, isLoading: visible.isLoading, loadFailed: visible.loadFailed,
      hasMore: visible.hasMore, loadMore, refresh, retire,
    };
  }, [isRunActive, loadMore, owner, projection, refresh, retire, runId, state]);
}
