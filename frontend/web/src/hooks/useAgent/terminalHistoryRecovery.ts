/** Retry only transient terminal-history failures within the current owner. */
export async function recoverTerminalHistory<T>(
  load: (signal: AbortSignal) => Promise<T>,
  signal: AbortSignal,
): Promise<T> {
  for (let attempt = 0; ; attempt += 1) {
    signal.throwIfAborted();
    const request = new AbortController();
    const abort = () => request.abort(signal.reason);
    signal.addEventListener("abort", abort, { once: true });
    const timeout = setTimeout(() => request.abort(new Error("History request timed out")), 10_000);
    let rejectAborted: (() => void) | undefined;
    try {
      return await Promise.race([
        load(request.signal),
        new Promise<never>((_resolve, reject) => {
          rejectAborted = () => reject(request.signal.reason);
          request.signal.addEventListener("abort", rejectAborted, { once: true });
          if (request.signal.aborted) rejectAborted();
        }),
      ]);
    } catch (error) {
      signal.throwIfAborted();
      const status = error && typeof error === "object" && "status" in error
        ? Number(error.status) : 0;
      if (attempt >= 2 || (status >= 400 && status < 500 && status !== 408 && status !== 429)) {
        throw error;
      }
    } finally {
      clearTimeout(timeout);
      signal.removeEventListener("abort", abort);
      if (rejectAborted) request.signal.removeEventListener("abort", rejectAborted);
    }
    await new Promise<void>((resolve, reject) => {
      const abort = () => {
        clearTimeout(timer);
        reject(signal.reason);
      };
      const timer = setTimeout(() => {
        signal.removeEventListener("abort", abort);
        resolve();
      }, 1_000 * (attempt + 1));
      signal.addEventListener("abort", abort, { once: true });
      if (signal.aborted) abort();
    });
  }
}
