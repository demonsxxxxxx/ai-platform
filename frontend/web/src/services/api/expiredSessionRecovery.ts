import { getAccessToken } from "./token";

export type ExpiredSessionRecoveryResult =
  | {
      status: "recovered";
      sessionMarker: string;
      principalMatchesPrevious: boolean;
    }
  | { status: "unavailable" }
  | { status: "cancelled" };

export type ExpiredSessionRecoveryHandler = (
  expectedMarker: string,
  signal: AbortSignal,
) => Promise<ExpiredSessionRecoveryResult>;

interface PendingRecovery {
  marker: string;
  controller: AbortController;
  promise: Promise<ExpiredSessionRecoveryResult>;
  waiters: number;
  settled: boolean;
  handler: ExpiredSessionRecoveryHandler;
}

let registeredHandler: ExpiredSessionRecoveryHandler | null = null;
let pendingRecovery: PendingRecovery | null = null;
let completedRecovery: {
  marker: string;
  result: Extract<ExpiredSessionRecoveryResult, { status: "recovered" }>;
} | null = null;

export function hasRecoveredSession(marker: string): boolean {
  return completedRecovery?.marker === marker
    && completedRecovery.result.sessionMarker === getAccessToken();
}

/** Hold new business requests until the provider has verified its new subject. */
export async function waitForActiveSessionRecovery(
  marker: string,
  signal?: AbortSignal,
): Promise<ExpiredSessionRecoveryResult | null> {
  if (!pendingRecovery || pendingRecovery.marker !== marker) return null;
  return waitForRecovery(pendingRecovery, signal);
}

export function registerExpiredSessionRecovery(
  handler: ExpiredSessionRecoveryHandler,
): () => void {
  registeredHandler = handler;
  return () => {
    if (registeredHandler === handler) {
      registeredHandler = null;
      completedRecovery = null;
      if (pendingRecovery?.handler === handler) {
        pendingRecovery.controller.abort();
        pendingRecovery = null;
      }
    }
  };
}

function waitForRecovery(
  pending: PendingRecovery,
  signal?: AbortSignal,
): Promise<ExpiredSessionRecoveryResult> {
  signal?.throwIfAborted();
  pending.waiters += 1;

  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      signal?.removeEventListener("abort", abort);
      pending.waiters = Math.max(0, pending.waiters - 1);
    };
    const abort = () => {
      finish();
      if (pending.waiters === 0 && !pending.settled) {
        pending.controller.abort(
          signal?.reason ?? new DOMException("Request cancelled", "AbortError"),
        );
      }
      reject(signal?.reason ?? new DOMException("Request cancelled", "AbortError"));
    };

    signal?.addEventListener("abort", abort, { once: true });
    pending.promise.then(
      (result) => {
        finish();
        resolve(result);
      },
      (error: unknown) => {
        finish();
        reject(error);
      },
    );
  });
}

/**
 * Run one provider-owned AD recovery for a missing 24-hour session context.
 * Request cancellation only cancels the shared operation after its last waiter
 * leaves; one cancelled request cannot interrupt another request's recovery.
 */
export async function recoverExpiredSession(
  expectedMarker: string,
  signal?: AbortSignal,
): Promise<ExpiredSessionRecoveryResult> {
  signal?.throwIfAborted();
  if (hasRecoveredSession(expectedMarker)) return completedRecovery!.result;
  if (!expectedMarker || getAccessToken() !== expectedMarker) {
    return { status: "cancelled" };
  }

  let pending = pendingRecovery;
  if (pending && pending.marker !== expectedMarker) {
    pending.controller.abort(new DOMException("Session changed", "AbortError"));
    pending = null;
  }
  if (!pending) {
    const handler = registeredHandler;
    if (!handler) return { status: "unavailable" };

    const attempt: PendingRecovery = {
      marker: expectedMarker,
      controller: new AbortController(),
      promise: Promise.resolve({ status: "cancelled" }),
      waiters: 0,
      settled: false,
      handler,
    };
    attempt.promise = Promise.resolve()
      .then(() => handler(expectedMarker, attempt.controller.signal))
      .catch((): ExpiredSessionRecoveryResult => ({ status: "unavailable" }))
      .then((result): ExpiredSessionRecoveryResult => {
        if (result.status === "recovered") {
          if (
            result.sessionMarker === expectedMarker ||
            getAccessToken() !== result.sessionMarker
          ) {
            return { status: "cancelled" };
          }
          completedRecovery = { marker: expectedMarker, result };
        }
        return result;
      })
      .finally(() => {
        attempt.settled = true;
        if (pendingRecovery === attempt) pendingRecovery = null;
      });
    pending = attempt;
    pendingRecovery = attempt;
  }

  const result = await waitForRecovery(pending, signal);
  signal?.throwIfAborted();
  if (result.status === "recovered") {
    return getAccessToken() === result.sessionMarker
      ? result
      : { status: "cancelled" };
  }
  return getAccessToken() === expectedMarker
    ? result
    : { status: "cancelled" };
}
