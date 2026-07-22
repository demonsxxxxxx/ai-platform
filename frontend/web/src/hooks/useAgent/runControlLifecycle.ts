import {
  fetchRunPlayback,
  type RunPlaybackResponse,
} from "../../services/api/runPlayback";
import {
  sessionApi,
  type ChatRunStatusResponse,
  type RunControlChildResponse,
  type RunControlMutationAction,
  type RunControlOperationResponse,
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

interface PendingRunControlOperation {
  version: 1;
  tenantId: string;
  userId: string;
  sessionId: string;
  sourceRunId: string;
  action: RunControlMutationAction;
  operationId: string;
}

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

const RUN_CONTROL_OPERATION_STORAGE_PREFIX =
  "ai-platform.run-control-operation.v1";
const UUID4_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

function operationStorage(): Storage | null {
  try {
    return globalThis.sessionStorage ?? null;
  } catch {
    return null;
  }
}

function operationStorageKey(parent: RunControlParentIdentity): string {
  return [
    RUN_CONTROL_OPERATION_STORAGE_PREFIX,
    parent.auth.tenantId,
    parent.auth.userId,
    parent.sessionId,
    parent.runId,
  ]
    .map(encodeURIComponent)
    .join(":");
}

function loadPendingOperation(
  parent: RunControlParentIdentity,
): PendingRunControlOperation | null {
  const storage = operationStorage();
  const key = operationStorageKey(parent);
  const validate = (
    value: Partial<PendingRunControlOperation>,
  ): PendingRunControlOperation | null => {
    const valid =
      value.version === 1 &&
      value.tenantId === parent.auth.tenantId &&
      value.userId === parent.auth.userId &&
      value.sessionId === parent.sessionId &&
      value.sourceRunId === parent.runId &&
      (value.action === "retry" || value.action === "resume") &&
      typeof value.operationId === "string" &&
      UUID4_PATTERN.test(value.operationId);
    return valid ? (value as PendingRunControlOperation) : null;
  };
  if (storage) {
    try {
      const raw = storage.getItem(key);
      if (raw) {
        const pending = validate(
          JSON.parse(raw) as Partial<PendingRunControlOperation>,
        );
        if (pending) {
          return pending;
        }
        storage.removeItem(key);
      }
    } catch {
      // Durable reload recovery is unavailable; no unverified value is replayed.
    }
  }
  return null;
}

function persistPendingOperation(
  parent: RunControlParentIdentity,
  pending: PendingRunControlOperation,
): boolean {
  const key = operationStorageKey(parent);
  const storage = operationStorage();
  if (!storage) return true;
  try {
    storage.setItem(key, JSON.stringify(pending));
  } catch {
    // The caller retains the pending value in this lifecycle instance, so the
    // first mutation and same-page resolver/replay path remain available.
  }
  return true;
}

function removePendingOperation(
  parent: RunControlParentIdentity,
  operationId: string,
): void {
  const key = operationStorageKey(parent);
  const storage = operationStorage();
  if (!storage) return;
  try {
    const raw = storage.getItem(key);
    if (!raw) return;
    const value = JSON.parse(raw) as Partial<PendingRunControlOperation>;
    if (value.operationId === operationId) storage.removeItem(key);
  } catch {
    // The resolver is already authoritative; storage cleanup is best effort.
  }
}

function createOpaqueOperationId(): string {
  const cryptoApi = globalThis.crypto;
  if (typeof cryptoApi?.randomUUID === "function") {
    return cryptoApi.randomUUID();
  }
  if (typeof cryptoApi?.getRandomValues !== "function") {
    throw new Error("control_operation_crypto_unavailable");
  }
  const bytes = cryptoApi.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0"));
  return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
}

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

function isTerminalPlaybackStatus(value: string | null): boolean {
  return value !== null && [
    "completed",
    "succeeded",
    "failed",
    "error",
    "cancelled",
    "canceled",
    "rejected",
  ].includes(value);
}

function isDefinitiveMutationRejection(error: ApiRequestError): boolean {
  // Only a server-confirmed authorization or precondition failure proves that
  // retry/resume did not create a child. Timeouts, throttling and every 5xx
  // can occur after commit, so recovery is GET-first; POST is eligible only
  // after the locked resolver proves authoritative absence.
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

function childFromExactOperation(
  response: RunControlChildResponse | RunControlOperationResponse | null,
  pending: PendingRunControlOperation,
  allowMissingOperationEcho: boolean,
): RunControlChild | null {
  const hasNoOperationEcho =
    allowMissingOperationEcho &&
    response?.action === undefined &&
    response?.operation_id === undefined &&
    response?.source_run_id === undefined;
  const hasExactOperationEcho =
    response?.action === pending.action &&
    response?.operation_id === pending.operationId &&
    (response.source_run_id === undefined ||
      response.source_run_id === pending.sourceRunId);
  if (
    !response ||
    (!hasNoOperationEcho && !hasExactOperationEcho) ||
    response.queue_admission === "pending" ||
    response.queue_admission === "unknown" ||
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
  private pendingOperation: PendingRunControlOperation | null = null;
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
    this.pendingOperation = loadPendingOperation(parent);
    const owner: RunControlOwner = {
      ...parent,
      id: parentIdentityKey(parent),
      abortController: new AbortController(),
      readSequence: 0,
      actionSequence: 0,
      phase: this.pendingOperation ? "unconfirmed" : "idle",
      mutationStarted: this.pendingOperation !== null,
    };
    this.owner = owner;
    this.publish({
      owner,
      phase: owner.phase,
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
    this.pendingOperation = null;
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
    const pending = this.pendingOperation;
    if (pending && this.callbacks) {
      const actionSequence = ++owner.actionSequence;
      void this.resolvePendingOperation(owner, pending, actionSequence, true);
      return;
    }
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
    const playbackIsTerminal = isTerminalPlaybackStatus(
      normalizedStatus(playback?.run?.status),
    );
    expectedOwner.phase =
      preservedPhase === "unconfirmed"
        ? "unconfirmed"
        : playbackIsTerminal
          ? "ready"
          : (preservedPhase ?? (unavailable ? "read_error" : "ready"));
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

    let pending: PendingRunControlOperation | null = null;
    if (action !== "cancel") {
      try {
        pending = {
          version: 1,
          tenantId: owner.auth.tenantId,
          userId: owner.auth.userId,
          sessionId: owner.sessionId,
          sourceRunId: owner.runId,
          action,
          operationId: createOpaqueOperationId(),
        };
      } catch (error) {
        owner.mutationStarted = true;
        owner.phase = "rejected";
        this.publishForOwner(owner, {
          phase: "rejected",
          rejectionMessage:
            error instanceof Error
              ? error.message
              : "control_operation_crypto_unavailable",
        });
        return;
      }
      if (!persistPendingOperation(owner, pending)) {
        owner.mutationStarted = true;
        owner.phase = "rejected";
        this.publishForOwner(owner, {
          phase: "rejected",
          rejectionMessage: "control_operation_storage_unavailable",
        });
        return;
      }
      this.pendingOperation = pending;
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
      const response = await this.requestMutation(
        action,
        owner,
        pending?.operationId,
      );
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

      if (!pending) return;
      if (
        await this.acceptOperationChild(
          owner,
          pending,
          response as RunControlChildResponse | null,
          actionSequence,
          true,
        )
      ) {
        return;
      }
      await this.resolvePendingOperation(owner, pending, actionSequence, true);
    } catch (error) {
      if (
        !this.isCurrentOwner(owner) ||
        owner.actionSequence !== actionSequence
      ) {
        return;
      }
      if (error instanceof ApiRequestError && isDefinitiveMutationRejection(error)) {
        if (pending) this.clearPendingOperation(owner, pending);
        this.publishRejected(owner, error.message);
        return;
      }
      if (pending) {
        await this.resolvePendingOperation(owner, pending, actionSequence, true);
        return;
      }
      // Cancellation keeps its existing parent GET convergence path. Retry and
      // resume never infer a child from the terminal parent.
      this.publishUnconfirmed(owner, true);
    }
  }

  private requestMutation(
    action: RunControlAction,
    owner: RunControlOwner,
    operationId?: string,
  ): Promise<RunControlChildResponse | { run_id: string; status: string } | null> {
    const options = { signal: owner.abortController.signal };
    if (action === "cancel") {
      return sessionApi.cancelRun(owner.runId, options);
    }
    if (!operationId) {
      return Promise.reject(new Error("control_operation_id_missing"));
    }
    if (action === "retry") {
      return sessionApi.retryRun(owner.runId, operationId, options);
    }
    return sessionApi.resumeRun(owner.runId, operationId, options);
  }

  private async resolvePendingOperation(
    owner: RunControlOwner,
    pending: PendingRunControlOperation,
    actionSequence: number,
    allowReplayAfterAbsence: boolean,
  ): Promise<void> {
    if (!this.isCurrentOperation(owner, pending, actionSequence)) return;
    this.publishUnconfirmed(owner);
    let resolution: RunControlOperationResponse;
    try {
      resolution = await sessionApi.resolveRunControlOperation(
        pending.sourceRunId,
        pending.action,
        pending.operationId,
        { signal: owner.abortController.signal },
      );
    } catch (error) {
      if (!this.isCurrentOperation(owner, pending, actionSequence)) return;
      if (error instanceof ApiRequestError && isDefinitiveMutationRejection(error)) {
        this.clearPendingOperation(owner, pending);
        this.publishRejected(owner, error.message);
      } else {
        this.publishUnconfirmed(owner);
        await this.refreshUnconfirmedReadiness(owner);
      }
      return;
    }
    if (!this.isCurrentOperation(owner, pending, actionSequence)) return;
    if (
      await this.acceptOperationChild(
        owner,
        pending,
        resolution,
        actionSequence,
        false,
      )
    ) {
      return;
    }
    const exactAbsence =
      resolution.source_run_id === pending.sourceRunId &&
      resolution.action === pending.action &&
      resolution.operation_id === pending.operationId &&
      resolution.status === "absent" &&
      resolution.run_id === null &&
      resolution.session_id === null;
    const exactPendingAdmission =
      resolution.source_run_id === pending.sourceRunId &&
      resolution.action === pending.action &&
      resolution.operation_id === pending.operationId &&
      resolution.queue_admission === "pending" &&
      typeof resolution.run_id === "string" &&
      resolution.run_id.length > 0 &&
      typeof resolution.session_id === "string" &&
      resolution.session_id.length > 0;
    if (
      (!exactAbsence && !exactPendingAdmission) ||
      !allowReplayAfterAbsence
    ) {
      this.publishUnconfirmed(owner);
      await this.refreshUnconfirmedReadiness(owner);
      return;
    }

    try {
      const replay = await this.requestMutation(
        pending.action,
        owner,
        pending.operationId,
      );
      if (!this.isCurrentOperation(owner, pending, actionSequence)) return;
      if (
        await this.acceptOperationChild(
          owner,
          pending,
          replay as RunControlChildResponse | null,
          actionSequence,
          true,
        )
      ) {
        return;
      }
      this.publishUnconfirmed(owner);
    } catch (error) {
      if (!this.isCurrentOperation(owner, pending, actionSequence)) return;
      if (error instanceof ApiRequestError && isDefinitiveMutationRejection(error)) {
        this.clearPendingOperation(owner, pending);
        this.publishRejected(owner, error.message);
      } else {
        this.publishUnconfirmed(owner);
      }
    }
  }

  private async acceptOperationChild(
    owner: RunControlOwner,
    pending: PendingRunControlOperation,
    response: RunControlChildResponse | RunControlOperationResponse | null,
    actionSequence: number,
    allowMissingOperationEcho: boolean,
  ): Promise<boolean> {
    const child = childFromExactOperation(
      response,
      pending,
      allowMissingOperationEcho,
    );
    if (!child || !this.isCurrentOperation(owner, pending, actionSequence)) {
      return false;
    }
    this.clearPendingOperation(owner, pending);
    owner.phase = "adopting";
    this.publishForOwner(owner, { phase: "adopting", child });
    const adoption = await this.callbacks?.adoptRunControlChild(owner, child);
    // The parent may replace this owner while loading the exact child. The old
    // owner stays silent across A→B→A races and created-unopened handoff.
    if (adoption === "superseded" || !this.isCurrentOwner(owner)) return true;
    return true;
  }

  private isCurrentOperation(
    owner: RunControlOwner,
    pending: PendingRunControlOperation,
    actionSequence: number,
  ): boolean {
    return (
      this.isCurrentOwner(owner) &&
      owner.actionSequence === actionSequence &&
      this.pendingOperation?.operationId === pending.operationId
    );
  }

  private clearPendingOperation(
    owner: RunControlOwner,
    pending: PendingRunControlOperation,
  ): void {
    removePendingOperation(owner, pending.operationId);
    if (this.pendingOperation?.operationId === pending.operationId) {
      this.pendingOperation = null;
    }
  }

  private publishRejected(owner: RunControlOwner, message: string): void {
    owner.phase = "rejected";
    this.publishForOwner(owner, {
      phase: "rejected",
      rejectionMessage: message,
    });
  }

  private publishUnconfirmed(
    owner: RunControlOwner,
    refreshParent = false,
  ): void {
    if (!this.isCurrentOwner(owner)) return;
    owner.phase = "unconfirmed";
    this.publishForOwner(owner, { phase: "unconfirmed", rejectionMessage: null });
    if (refreshParent) void this.refresh(owner);
  }

  private async refreshUnconfirmedReadiness(
    owner: RunControlOwner,
  ): Promise<void> {
    if (!this.isCurrentOwner(owner)) return;
    const readSequence = ++owner.readSequence;
    try {
      const readiness = await sessionApi.getStatus(owner.sessionId, owner.runId, {
        signal: owner.abortController.signal,
      });
      if (
        !this.isCurrentOwner(owner) ||
        owner.readSequence !== readSequence ||
        owner.phase !== "unconfirmed"
      ) {
        return;
      }
      this.publishForOwner(owner, { readiness });
    } catch {
      // The exact operation remains persisted and unconfirmed for a later GET.
    }
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
    const playbackStatus = normalizedStatus(next.playback?.run?.status);
    const status = isTerminalPlaybackStatus(playbackStatus)
      ? playbackStatus
      : normalizedStatus(
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
