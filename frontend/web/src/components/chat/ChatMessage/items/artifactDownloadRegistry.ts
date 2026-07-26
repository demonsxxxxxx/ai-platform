export type ArtifactDownloadState = "idle" | "downloading" | "failed";

export interface ArtifactDownloadScopeContext {
  tenantId: string;
  userId: string;
  roles: readonly string[];
  isActive: boolean;
  sessionId: string;
  key: string;
}

export interface ArtifactDownloadScope extends ArtifactDownloadScopeContext {
  contextKey: string;
  messageId: string;
  agentId?: string;
  key: string;
}

export interface ArtifactDownloadController {
  getState: () => ArtifactDownloadState;
  subscribe: (listener: (state: ArtifactDownloadState) => void) => () => void;
  download: (downloadArtifact: () => Promise<boolean>) => Promise<void>;
}

interface ArtifactDownloadRegistryOptions {
  maxEntries?: number;
  maxEntriesPerScope?: number;
  now?: () => number;
  settledTtlMs?: number;
}

interface RegistryEntry {
  artifactKey: string;
  contextKey: string;
  isDisposed: boolean;
  isInFlight: boolean;
  isInvalidated: boolean;
  lastTouchedAt: number;
  listeners: Set<(state: ArtifactDownloadState) => void>;
  scopeKey: string;
  settledAt?: number;
  state: ArtifactDownloadState;
  timeout?: ReturnType<typeof setTimeout>;
}

const DEFAULT_MAX_ENTRIES_PER_SCOPE = 64;
const DEFAULT_MAX_ENTRIES = 256;
const DEFAULT_SETTLED_TTL_MS = 30_000;

function serializeIdentity(values: readonly (string | boolean | readonly string[])[]) {
  return JSON.stringify(values);
}

/**
 * Returns no shared scope until an authenticated, active user and session are
 * all available. That fail-closed path prevents unscoped state from crossing
 * principals while the normal authenticated view is initializing.
 */
export function createArtifactDownloadScopeContext(input: {
  isActive?: boolean;
  roles?: readonly string[];
  sessionId?: string | null;
  tenantId?: string;
  userId?: string;
}): ArtifactDownloadScopeContext | undefined {
  if (
    !input.isActive ||
    !input.tenantId ||
    !input.userId ||
    !input.sessionId
  ) {
    return undefined;
  }

  const roles = [...new Set(input.roles ?? [])].sort();
  return {
    tenantId: input.tenantId,
    userId: input.userId,
    roles,
    isActive: true,
    sessionId: input.sessionId,
    key: serializeIdentity([
      input.tenantId,
      input.userId,
      roles,
      true,
      input.sessionId,
    ]),
  };
}

export function createArtifactDownloadScope(
  context: ArtifactDownloadScopeContext | undefined,
  messageId: string | undefined,
  agentId?: string,
): ArtifactDownloadScope | undefined {
  if (!context || !messageId) {
    return undefined;
  }

  return {
    ...context,
    contextKey: context.key,
    messageId,
    agentId,
    key: serializeIdentity([
      context.tenantId,
      context.userId,
      context.roles,
      context.isActive,
      context.sessionId,
      messageId,
      agentId ?? "",
    ]),
  };
}

export function createSubagentArtifactDownloadScope(
  scope: ArtifactDownloadScope | undefined,
  agentId: string,
): ArtifactDownloadScope | undefined {
  if (!scope || !agentId) {
    return undefined;
  }

  return createArtifactDownloadScope(
    { ...scope, key: scope.contextKey },
    scope.messageId,
    agentId,
  );
}

function getArtifactKey(scope: ArtifactDownloadScope, artifactId: string): string {
  return `${scope.key}:${JSON.stringify(artifactId)}`;
}

function createUnavailableController(): ArtifactDownloadController {
  return {
    getState: () => "failed",
    subscribe(listener) {
      listener("failed");
      return () => undefined;
    },
    async download() {
      // The registry is at capacity with active work. Refusing this operation
      // preserves the one-request invariant instead of evicting an active card.
    },
  };
}

export interface ArtifactDownloadRegistry {
  clearScope: (context: ArtifactDownloadScopeContext) => void;
  collectExpired: () => void;
  get: (
    scope: ArtifactDownloadScope | undefined,
    artifactId: string,
  ) => ArtifactDownloadController | undefined;
  size: (context?: ArtifactDownloadScopeContext) => number;
}

/**
 * Shared request lifecycle store. Entries contain only opaque scope/artifact
 * identities and UI state; callers retain the actual download callback and URL.
 */
export function createArtifactDownloadRegistry(
  options: ArtifactDownloadRegistryOptions = {},
): ArtifactDownloadRegistry {
  const maxEntriesPerScope =
    options.maxEntriesPerScope ?? DEFAULT_MAX_ENTRIES_PER_SCOPE;
  const maxEntries = options.maxEntries ?? DEFAULT_MAX_ENTRIES;
  const settledTtlMs = options.settledTtlMs ?? DEFAULT_SETTLED_TTL_MS;
  const now = options.now ?? Date.now;
  const entries = new Map<string, RegistryEntry>();

  const dispose = (entry: RegistryEntry) => {
    entry.isDisposed = true;
    if (entry.timeout) {
      clearTimeout(entry.timeout);
    }
    entry.listeners.clear();
    entries.delete(entry.artifactKey);
  };

  const emit = (entry: RegistryEntry) => {
    entry.listeners.forEach((listener) => listener(entry.state));
  };

  const collectExpired = () => {
    const expiredAt = now() - settledTtlMs;
    entries.forEach((entry) => {
      if (!entry.isInFlight && entry.settledAt !== undefined && entry.settledAt <= expiredAt) {
        dispose(entry);
      }
    });
  };

  const scheduleCleanup = (entry: RegistryEntry) => {
    if (entry.isDisposed || entry.isInFlight || entry.settledAt === undefined) {
      return;
    }
    if (entry.timeout) {
      clearTimeout(entry.timeout);
    }
    entry.timeout = setTimeout(() => {
      if (
        !entry.isDisposed &&
        !entry.isInFlight &&
        entry.settledAt !== undefined &&
        now() - entry.settledAt >= settledTtlMs
      ) {
        dispose(entry);
      }
    }, settledTtlMs);
  };

  const createController = (entry: RegistryEntry): ArtifactDownloadController => ({
    getState: () => entry.state,
    subscribe(listener) {
      if (entry.isDisposed || entry.isInvalidated) {
        return () => undefined;
      }
      entry.listeners.add(listener);
      listener(entry.state);
      return () => {
        entry.listeners.delete(listener);
      };
    },
    async download(downloadArtifact) {
      if (entry.isDisposed || entry.isInvalidated || entry.isInFlight) {
        return;
      }

      entry.isInFlight = true;
      entry.settledAt = undefined;
      entry.lastTouchedAt = now();
      if (entry.timeout) {
        clearTimeout(entry.timeout);
        entry.timeout = undefined;
      }
      entry.state = "downloading";
      emit(entry);
      try {
        const succeeded = await downloadArtifact();
        if (!entry.isDisposed && !entry.isInvalidated) {
          entry.state = succeeded ? "idle" : "failed";
          emit(entry);
        }
      } catch {
        if (!entry.isDisposed && !entry.isInvalidated) {
          entry.state = "failed";
          emit(entry);
        }
      } finally {
        if (entry.isInvalidated && !entry.isDisposed) {
          dispose(entry);
        } else if (!entry.isDisposed) {
          entry.isInFlight = false;
          entry.lastTouchedAt = now();
          entry.settledAt = entry.lastTouchedAt;
          scheduleCleanup(entry);
        }
      }
    },
  });

  const evictSettledEntries = (
    predicate: (entry: RegistryEntry) => boolean,
    maximum: number,
  ) => {
    const scopedEntries = [...entries.values()]
      .filter(predicate)
      .sort((left, right) => left.lastTouchedAt - right.lastTouchedAt);
    while (scopedEntries.length >= maximum) {
      const settledEntry = scopedEntries.find((entry) => !entry.isInFlight);
      if (!settledEntry) {
        return false;
      }
      dispose(settledEntry);
      scopedEntries.splice(scopedEntries.indexOf(settledEntry), 1);
    }
    return true;
  };

  return {
    clearScope(context) {
      entries.forEach((entry) => {
        if (entry.contextKey === context.key) {
          if (entry.isInFlight) {
            entry.isInvalidated = true;
            entry.listeners.clear();
          } else {
            dispose(entry);
          }
        }
      });
    },
    collectExpired,
    get(scope, artifactId) {
      if (!scope || !artifactId) {
        return undefined;
      }
      collectExpired();
      const artifactKey = getArtifactKey(scope, artifactId);
      const existing = entries.get(artifactKey);
      if (existing) {
        if (existing.isInvalidated) {
          return createUnavailableController();
        }
        existing.lastTouchedAt = now();
        return createController(existing);
      }
      if (
        !evictSettledEntries(
          (entry) => entry.scopeKey === scope.key,
          maxEntriesPerScope,
        ) ||
        !evictSettledEntries(() => true, maxEntries)
      ) {
        return createUnavailableController();
      }
      const entry: RegistryEntry = {
        artifactKey,
        contextKey: scope.contextKey,
        isDisposed: false,
        isInFlight: false,
        isInvalidated: false,
        lastTouchedAt: now(),
        listeners: new Set(),
        scopeKey: scope.key,
        state: "idle",
      };
      entries.set(artifactKey, entry);
      return createController(entry);
    },
    size(context) {
      if (!context) {
        return entries.size;
      }
      return [...entries.values()].filter(
        (entry) => entry.contextKey === context.key,
      ).length;
    },
  };
}

const artifactDownloadRegistry = createArtifactDownloadRegistry();

export function clearArtifactDownloadScope(
  context: ArtifactDownloadScopeContext,
) {
  artifactDownloadRegistry.clearScope(context);
}

export function getArtifactDownloadController(
  scope: ArtifactDownloadScope | undefined,
  artifactId: string,
) {
  return artifactDownloadRegistry.get(scope, artifactId);
}
