import { useEffect, useState } from "react";

/** A result belongs only to its query and live effect, even if transport ignores abort. */
export function useUsageReport<T>(load: (signal: AbortSignal) => Promise<T>) {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ owner: typeof load; attempt: number; data?: T; error?: unknown }>();
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal).then(
      (data) => { if (!controller.signal.aborted) setResult({ owner: load, attempt, data }); },
      (error: unknown) => { if (!controller.signal.aborted) setResult({ owner: load, attempt, error }); },
    );
    return () => controller.abort();
  }, [load, attempt]);
  const current = result?.owner === load && result.attempt === attempt ? result : undefined;
  return { data: current?.data, error: current?.error, loading: !current, retry: () => setAttempt((value) => value + 1) };
}
