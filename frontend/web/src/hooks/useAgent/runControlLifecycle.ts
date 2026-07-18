import {
  fetchRunPlayback,
  type RunPlaybackResponse,
} from "../../services/api/runPlayback";
import {
  sessionApi,
  type ChatRunStatusResponse,
  type RunControlChildResponse,
} from "../../services/api/session";
import { ApiRequestError } from "../../services/api/fetch";

export type RunControlPhase =
  | "idle"
  | "loading"
  | "ready"
  | "read_error"
  | "cancelling"
  | "cancel_requested"
  | "retrying"
  | "resuming"
  | "adopting"
  | "created_unopened"
  | "unconfirmed"
  | "rejected"
  | "reconnecting";

export type RunControlAction = "cancel" | "retry" | "resume";

/**
 * Public, non-secret facts that distinguish one browser authentication
 * incarnation from another. Values are used only as an in-memory ownership
 * fence; the lifecycle never renders or persists them.
 */
export interface RunControlAuthIdentity {
  /** Coordinator-issued, crypto-random browser-auth incarnation. */
  incarnation: string | null;
  sessionMarker: string | null;
  tenantId: string;
  userId: string;
  roles: readonly string[];
  permissions: readonly string[];
  isAdmin: boolean;
  isActive: boolean;
}

export interface RunControlParentIdentity {
  chatHistoryGeneration: number;
  authRevision: number;
  auth: RunControlAuthIdentity;
  sessionId: string;
  runId: string;
}

/**
 * A single authoritative owner for all asynchronous Run Playback work.
 *
 * Abort is resource cleanup only. `RunControlLifecycle.isCurrentOwner` checks
 * the identity, history generation, session/run parent and auth revision at
 * every publication boundary, so a transport that ignores AbortSignal cannot
 * publish stale state.
 */
export interface RunControlOwner extends RunControlParentIdentity {
  id: string;
  abortController: AbortController;
  readSequence: number;
  actionSequence: number;
  phase: RunControlPhase;
  mutationStarted: boolean;
}

export interface RunControlChild {
  sessionId: string;
  runId: string;
  status: string | null;
}

export type RunControlChildAdoption =
  | "adopted"
  | "created_unopened"
  | "superseded";

export interface RunControlLifecycleCallbacks {
  /** The parent alone may load/adopt a child transcript and route. */
  adoptRunControlChild: (
    owner: RunControlOwner,
    child: RunControlChild,
  ) => Promise<RunControlChildAdoption>;
  /** Existing useAgent reconnect/reconcile path; it remains terminal writer. */
  reconnectRunControlOwner: (owner: RunControlOwner) => Promise<void>;
}

export interface RunControlSnapshot {
  revision: number;
  owner: RunControlOwner | null;
  phase: RunControlPhase;
  playback: RunPlaybackResponse | null;
  readiness: ChatRunStatusResponse | null;
  readError: "unavailable" | null;
  rejectionMessage: string | null;
  child: RunControlChild | null;
  canCancel: boolean;
  canRetry: boolean;
  canResume: boolean;
  canReconnect: boolean;
  isBusy: boolean;
}

const EMPTY_SNAPSHOT: RunControlSnapshot = {
  revision: 0,
  owner: null,
  phase: "idle",
  playback: null,
  readiness: null,
  readError: null,
  rejectionMessage: null,
  child: null,
  canCancel: false,
  canRetry: false,
  canResume: false,
  canReconnect: false,
  isBusy: false,
};

function normalizedStatus(value: unknown): string | null {
  return typeof value === "string" && value.trim()
    ? value.trim().toLowerCase()
    : null;
}

function isActiveStatus(value: string | null): boolean {
  return value !== null && ["pending", "queued", "running", "processing"].includes(value);
}

function isRetryableStatus(value: string | null): boolean {
  return value !== null && ["failed", "error", "cancelled"].includes(value);
}

function isResumableStatus(value: string | null): boolean {
  return value !== null && ["failed", "cancelled"].includes(value);
}

function isDefinitiveMutationRejection(error: ApiRequestError): boolean {
  // Only a server-confirmed authorization or precondition failure proves that
  // retry/resume did not create a child. Timeouts, throttling and every 5xx
  // can occur after commit, so they must remain unconfirmed and GET-only.
  return [401, 403, 404, 409, 410, 412, 422].includes(error.status);
}

function parentIdentityKey(parent: RunControlParentIdentity): string {
  return JSON.stringify([
    parent.chatHistoryGeneration,
    parent.authRevision,
    parent.sessionId,
    parent.runId,
    parent.auth.incarnation,
    parent.auth.sessionMarker,
    parent.auth.tenantId,
    parent.auth.userId,
    [...parent.auth.roles],
    [...parent.auth.permissions],
    parent.auth.isAdmin,
    parent.auth.isActive,
  ]);
}

function ownerIdentityKey(owner: RunControlOwner): string {
  return parentIdentityKey(owner);
}

function childFromResponse(response: RunControlChildResponse | null): RunControlChild | null {
  if (
    !response ||
    typeof response.session_id !== "string" ||
    !response.session_id.trim() ||
    typeof response.run_id !== "string" ||
    !response.run_id.trim()
  ) {
    return null;
  }
  return {
    sessionId: response.session_id,
    runId: response.run_id,
    status: normalizedStatus(response.status),
  };
}

function isBusyPhase(phase: RunControlPhase): boolean {
  return [
    "loading",
    "cancelling",
    "retrying",
    "resuming",
    "adopting",
    "reconnecting",
  ].includes(phase);
}

/**
 * Owns playback reads and run-control mutations for exactly one parent run.
 * It deliberately has no transcript or routing setter: child adoption must
 * cross the `useAgent.adoptRunControlChild` callback supplied by its parent.
 */
export class RunControlLifecycle {
  private callbacks: RunControlLifecycleCallbacks | null = null;
  private parent: RunControlParentIdentity | null = null;
  private owner: RunControlOwner | null = null;
  private snapshot: RunControlSnapshot = EMPTY_SNAPSHOT;
  private readonly listeners = new Set<() => void>();

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = (): RunControlSnapshot => this.snapshot;

  configure(callbacks: RunControlLifecycleCallbacks): void {
    this.callbacks = callbacks;
  }

  /** Bind the current parent. Any identity change aborts and replaces old work. */
  bindParent(parent: RunControlParentIdentity | null): RunControlOwner | null {
    if (parent === null) {
      this.invalidate();
      this.parent = null;
      return null;
    }

    const previousOwner = this.owner;
    if (
      previousOwner !== null &&
      ownerIdentityKey(previousOwner) === parentIdentityKey(parent)
    ) {
      this.parent = parent;
      return previousOwner;
    }

    this.abortOwner();
    this.parent = parent;
    const owner: RunControlOwner = {
      ...parent,
      id: parentIdentityKey(parent),
      abortController: new AbortController(),
      readSequence: 0,
      actionSequence: 0,
      phase: "idle",
      mutationStarted: false,
    };
    this.owner = owner;
    this.publish({
      owner,
      phase: "idle",
      playback: null,
      readiness: null,
      readError: null,
      rejectionMessage: null,
      child: null,
    });
    return owner;
  }

  /** Invalidate before React clears or replaces its parent history. */
  invalidate(): void {
    this.abortOwner();
    this.owner = null;
    this.publish({
      owner: null,
      phase: "idle",
      playback: null,
      readiness: null,
      readError: null,
      rejectionMessage: null,
      child: null,
    });
  }

  isCurrentOwner(owner: RunControlOwner): boolean {
    const parent = this.parent;
    return (
      this.owner === owner &&
      parent !== null &&
      ownerIdentityKey(owner) === parentIdentityKey(parent)
    );
  }

  /** Start the initial, GET-only playback/readiness read for the current owner. */
  open(): void {
    const owner = this.owner;
    if (!owner || !this.isCurrentOwner(owner)) return;
    void this.refresh(owner);
  }

  /** Refresh only reads; it never writes parent transcript or terminal state. */
  async refresh(expectedOwner: RunControlOwner | null = this.owner): Promise<void> {
    if (!expectedOwner || !this.isCurrentOwner(expectedOwner)) return;
    const readSequence = ++expectedOwner.readSequence;
    const preservedPhase =
      expectedOwner.phase === "cancel_requested" ||
      expectedOwner.phase === "unconfirmed"
        ? expectedOwner.phase
        : null;
    if (preservedPhase === null) {
      expectedOwner.phase = "loading";
      this.publishForOwner(expectedOwner, {
        phase: "loading",
        readError: null,
        rejectionMessage: null,
      });
    }

    const signal = expectedOwner.abortController.signal;
    const [playbackResult, readinessResult] = await Promise.allSettled([
      fetchRunPlayback(expectedOwner.runId, {}, { signal }),
      sessionApi.getStatus(expectedOwner.sessionId, expectedOwner.runId, { signal }),
    ]);

    if (
      !this.isCurrentOwner(expectedOwner) ||
      expectedOwner.readSequence !== readSequence
    ) {
      return;
    }

    const playback =
      playbackResult.status === "fulfilled" ? playbackResult.value : null;
    const readiness =
      readinessResult.status === "fulfilled" ? readinessResult.value : null;
    const unavailable = playback === null && readiness === null;
    expectedOwner.phase =
      preservedPhase ?? (unavailable ? "read_error" : "ready");
    this.publishForOwner(expectedOwner, {
      phase: expectedOwner.phase,
      playback: playback ?? this.snapshot.playback,
      readiness: readiness ?? this.snapshot.readiness,
      readError: unavailable ? "unavailable" : null,
    });
  }

  async cancel(): Promise<void> {
    await this.mutate("cancel");
  }

  async retry(): Promise<void> {
    await this.mutate("retry");
  }

  async resume(): Promise<void> {
    await this.mutate("resume");
  }

  /** Reopen is intentionally GET-only after a known child was created. */
  async reopenChild(): Promise<void> {
    const owner = this.owner;
    const child = this.snapshot.child;
    if (
      !owner ||
      !child ||
      this.snapshot.phase !== "created_unopened" ||
      !this.isCurrentOwner(owner) ||
      !this.callbacks
    ) {
      return;
    }
    owner.phase = "adopting";
    this.publishForOwner(owner, { phase: "adopting", rejectionMessage: null });
    const result = await this.callbacks.adoptRunControlChild(owner, child);
    if (result === "superseded" || !this.isCurrentOwner(owner)) return;
    // `created_unopened` is restored by the parent through retainCreatedUnopened.
    // The current owner intentionally makes no transcript or route writes here.
    if (result === "adopted") return;
  }

  /** Delegate reconnect to useAgent's existing single transport/reconcile path. */
  async reconnect(): Promise<void> {
    const owner = this.owner;
    if (!owner || !this.callbacks || !this.isCurrentOwner(owner)) return;
    owner.phase = "reconnecting";
    this.publishForOwner(owner, { phase: "reconnecting", rejectionMessage: null });
    try {
      await this.callbacks.reconnectRunControlOwner(owner);
    } catch {
      // The existing reconcile path owns terminal/error projection. Keep this
      // panel truthful by performing only its normal GET refresh below.
    }
    if (!this.isCurrentOwner(owner)) return;
    await this.refresh(owner);
  }

  /**
   * The parent calls this after a child mutation was acknowledged but its one
   * loadHistory attempt returned null/rejected. It creates a fresh owner at
   * the current history generation so Reopen can only issue a GET adoption.
   */
  retainCreatedUnopened(
    parent: RunControlParentIdentity,
    child: RunControlChild,
  ): void {
    const owner = this.bindParent(parent);
    if (!owner || !this.isCurrentOwner(owner)) return;
    owner.phase = "created_unopened";
    this.publishForOwner(owner, {
      phase: "created_unopened",
      child,
      rejectionMessage: null,
      readError: null,
    });
  }

  private async mutate(action: RunControlAction): Promise<void> {
    const owner = this.owner;
    if (
      !owner ||
      !this.callbacks ||
      owner.mutationStarted ||
      !this.isCurrentOwner(owner)
    ) {
      return;
    }

    owner.mutationStarted = true;
    const actionSequence = ++owner.actionSequence;
    const phase: RunControlPhase =
      action === "cancel"
        ? "cancelling"
        : action === "retry"
          ? "retrying"
          : "resuming";
    owner.phase = phase;
    this.publishForOwner(owner, { phase, rejectionMessage: null, readError: null });

    try {
      const response = await this.requestMutation(action, owner);
      if (
        !this.isCurrentOwner(owner) ||
        owner.actionSequence !== actionSequence
      ) {
        return;
      }

      if (action === "cancel") {
        // A cancel acknowledgement is not a terminal result. SSE/reconcile and
        // terminal history hydration remain useAgent's sole convergence writer.
        owner.phase = "cancel_requested";
        this.publishForOwner(owner, { phase: "cancel_requested" });
        void this.refresh(owner);
        return;
      }

      const child = childFromResponse(response as RunControlChildResponse | null);
      if (!child) {
        this.publishUnconfirmed(owner);
        return;
      }
      owner.phase = "adopting";
      this.publishForOwner(owner, { phase: "adopting", child });
      const adoption = await this.callbacks.adoptRunControlChild(owner, child);
      // The parent may have replaced this owner while loading the child. A
      // superseded parent must be completely silent, including A→B→A races.
      if (adoption === "superseded" || !this.isCurrentOwner(owner)) return;
      if (adoption === "adopted") return;
      // `created_unopened` is published by retainCreatedUnopened with a fresh
      // owner. Do not let this old owner overwrite that newer snapshot.
    } catch (error) {
      if (
        !this.isCurrentOwner(owner) ||
        owner.actionSequence !== actionSequence
      ) {
        return;
      }
      if (error instanceof ApiRequestError && isDefinitiveMutationRejection(error)) {
        owner.phase = "rejected";
        this.publishForOwner(owner, {
          phase: "rejected",
          rejectionMessage: error.message,
        });
        return;
      }
      // A lost mutation response is unknown, not failed. Do not replay a POST;
      // refresh only playback/readiness through the existing GET transports.
      this.publishUnconfirmed(owner);
    }
  }

  private requestMutation(
    action: RunControlAction,
    owner: RunControlOwner,
  ): Promise<RunControlChildResponse | { run_id: string; status: string } | null> {
    const options = { signal: owner.abortController.signal };
    if (action === "cancel") {
      return sessionApi.cancelRun(owner.runId, options);
    }
    if (action === "retry") {
      return sessionApi.retryRun(owner.runId, options);
    }
    return sessionApi.resumeRun(owner.runId, options);
  }

  private publishUnconfirmed(owner: RunControlOwner): void {
    if (!this.isCurrentOwner(owner)) return;
    owner.phase = "unconfirmed";
    this.publishForOwner(owner, { phase: "unconfirmed", rejectionMessage: null });
    void this.refresh(owner);
  }

  private abortOwner(): void {
    if (this.owner?.abortController) {
      this.owner.abortController.abort();
    }
  }

  private publishForOwner(
    owner: RunControlOwner,
    patch: Partial<Omit<RunControlSnapshot, "revision">>,
  ): void {
    if (!this.isCurrentOwner(owner)) return;
    this.publish({ ...patch, owner });
  }

  private publish(
    patch: Partial<Omit<RunControlSnapshot, "revision">>,
  ): void {
    const next = { ...this.snapshot, ...patch };
    const status = normalizedStatus(
      next.readiness?.raw_status ??
        next.readiness?.status ??
        next.playback?.run?.status,
    );
    const mutationAvailable = next.owner !== null && !next.owner.mutationStarted;
    const busy = isBusyPhase(next.phase);
    this.snapshot = {
      ...next,
      revision: this.snapshot.revision + 1,
      isBusy: busy,
      canCancel: mutationAvailable && isActiveStatus(status),
      canRetry: mutationAvailable && isRetryableStatus(status),
      canResume: mutationAvailable && isResumableStatus(status),
      canReconnect: next.owner !== null && isActiveStatus(status) && !busy,
    };
    this.listeners.forEach((listener) => listener());
  }
}
