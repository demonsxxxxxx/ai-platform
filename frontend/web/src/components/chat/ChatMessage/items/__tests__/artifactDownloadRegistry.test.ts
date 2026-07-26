import assert from "node:assert/strict";
import test from "node:test";
import {
  createArtifactDownloadRegistry,
  createArtifactDownloadScope,
  createArtifactDownloadScopeContext,
  createSubagentArtifactDownloadScope,
} from "../artifactDownloadRegistry";

function createContext(overrides: Partial<{
  sessionId: string;
  tenantId: string;
  userId: string;
  roles: string[];
}> = {}) {
  return createArtifactDownloadScopeContext({
    tenantId: overrides.tenantId ?? "tenant-a",
    userId: overrides.userId ?? "user-a",
    roles: overrides.roles ?? ["member"],
    isActive: true,
    sessionId: overrides.sessionId ?? "session-a",
  })!;
}

test("survives observer disposal and recreates one pending artifact request for the same scope", async () => {
  const registry = createArtifactDownloadRegistry();
  const context = createContext();
  const scope = createArtifactDownloadScope(context, "message-a")!;
  const firstController = registry.get(scope, "artifact-a")!;
  const states: string[] = [];
  const unsubscribe = firstController.subscribe((state) => states.push(state));
  let calls = 0;
  let resolveDownload: ((value: boolean) => void) | undefined;

  const pending = firstController.download(
    () =>
      new Promise<boolean>((resolve) => {
        calls += 1;
        resolveDownload = resolve;
      }),
  );
  unsubscribe();

  const remountedController = registry.get(scope, "artifact-a")!;
  const remountedStates: string[] = [];
  const unsubscribeRemounted = remountedController.subscribe((state) =>
    remountedStates.push(state),
  );
  await remountedController.download(async () => {
    calls += 1;
    return true;
  });

  assert.equal(calls, 1);
  assert.equal(remountedController.getState(), "downloading");
  assert.deepEqual(states, ["idle", "downloading"]);
  assert.deepEqual(remountedStates, ["downloading"]);

  resolveDownload?.(false);
  await pending;
  assert.equal(remountedController.getState(), "failed");
  await remountedController.download(async () => {
    calls += 1;
    return true;
  });
  assert.equal(calls, 2);
  assert.equal(remountedController.getState(), "idle");
  unsubscribeRemounted();
});

test("makes a changed authenticated scope inaccessible and ignores its stale completion", async () => {
  const registry = createArtifactDownloadRegistry();
  const oldContext = createContext({ roles: ["member"] });
  const oldScope = createArtifactDownloadScope(oldContext, "message-a")!;
  const oldController = registry.get(oldScope, "artifact-a")!;
  let resolveDownload: ((value: boolean) => void) | undefined;
  const pending = oldController.download(
    () => new Promise<boolean>((resolve) => {
      resolveDownload = resolve;
    }),
  );

  registry.clearScope(oldContext);
  const changedContext = createContext({ roles: ["admin"] });
  const changedScope = createArtifactDownloadScope(changedContext, "message-a")!;
  const changedController = registry.get(changedScope, "artifact-a")!;
  assert.equal(changedController.getState(), "idle");

  resolveDownload?.(false);
  await pending;
  assert.equal(changedController.getState(), "idle");
  assert.equal(registry.size(oldContext), 0);
});

test("bounds settled state while retaining active requests and shares the subagent contract", async () => {
  let now = 0;
  const registry = createArtifactDownloadRegistry({
    maxEntriesPerScope: 2,
    settledTtlMs: 10,
    now: () => now,
  });
  const context = createContext();
  const scope = createArtifactDownloadScope(context, "message-a")!;
  const nestedScope = createSubagentArtifactDownloadScope(scope, "agent-a")!;
  const first = registry.get(scope, "artifact-a")!;
  await first.download(async () => false);
  now = 20;
  registry.collectExpired();
  assert.equal(registry.size(context), 0);

  const second = registry.get(nestedScope, "artifact-a")!;
  assert.notEqual(first, second);
  const active = registry.get(scope, "artifact-active")!;
  let releaseActive: ((value: boolean) => void) | undefined;
  const pending = active.download(
    () => new Promise<boolean>((resolve) => {
      releaseActive = resolve;
    }),
  );
  now = 100;
  registry.collectExpired();
  assert.equal(active.getState(), "downloading");
  assert.equal(registry.get(scope, "artifact-active")!.getState(), "downloading");
  releaseActive?.(true);
  await pending;
});

test("does not create a cross-principal shared scope while authentication is incomplete", () => {
  assert.equal(
    createArtifactDownloadScopeContext({
      tenantId: "tenant-a",
      userId: "user-a",
      isActive: true,
      sessionId: null,
    }),
    undefined,
  );
});
