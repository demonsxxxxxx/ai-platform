import assert from "node:assert/strict";
import test, { after } from "node:test";
import { installSelectTestDom } from "../../common/__tests__/selectTestDom";
import { createLobeTestRoot } from "../../common/__tests__/lobeUiTestRoot";
import { installBrowserAuthTestDb } from "../../../hooks/__tests__/browserAuthTestDb";
import type { BackendSession } from "../../../services/api/session";
import type { SessionSidebarSessionSource } from "../SessionSidebar";

const dom = installSelectTestDom();
installBrowserAuthTestDb();
Object.assign(globalThis, {
  localStorage: window.localStorage,
  sessionStorage: window.sessionStorage,
});
class Observer {
  observe() {}
  unobserve() {}
  disconnect() {}
}
Object.assign(globalThis, {
  IntersectionObserver: Observer,
  ResizeObserver: Observer,
});
Object.assign(window, {
  IntersectionObserver: Observer,
  ResizeObserver: Observer,
});
after(() => dom.close());

const React = await import("react");
const { MemoryRouter } = await import("react-router-dom");
const { AuthProvider } = await import("../../../hooks/useAuth");
const { ThemeProvider } = await import("../../../contexts/ThemeContext");
const { LobeUiProvider } = await import("../../../contexts/LobeUiProvider");
const { SessionSidebar } = await import("../SessionSidebar");
const { authApi } = await import("../../../services/api/auth");
const { sessionApi } = await import("../../../services/api/session");

function session(id: string, unread_count = 0): BackendSession {
  return {
    id,
    agent_id: "general-agent",
    created_at: "2026-10-19T08:00:00Z",
    updated_at: "2026-10-19T09:30:00Z",
    is_active: true,
    metadata: {},
    unread_count,
    name: `Session ${id}`,
  };
}

function source(
  sessions: BackendSession[],
): SessionSidebarSessionSource {
  return {
    sessions,
    isLoading: false,
    isLoadingMore: false,
    hasMore: false,
    loadMore: async () => undefined,
    softRefresh: async () => undefined,
    prependSession: () => undefined,
    removeSession: () => undefined,
    updateSession: () => undefined,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}

test("SessionSidebar registers an identity-scoped adapter and retires stale callbacks", async () => {
  const originalBootstrap = authApi.bootstrapAuthContext;
  const originalCurrentUser = authApi.getCurrentUser;
  authApi.bootstrapAuthContext = async (request) => ({
    status: "ready",
    protocol_version: 2,
    generation: request.generation,
  });
  authApi.getCurrentUser = async () => ({
    id: "user-a",
    tenant_id: "tenant-a",
    username: "user-a",
    email: "user-a@example.test",
    roles: [],
    permissions: [],
    is_admin: false,
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  });

  const container = document.createElement("div");
  document.body.append(container);
  const root = await createLobeTestRoot(container);
  let registered: import("@assistant-ui/react").ExternalStoreThreadListAdapter | undefined;
  let registrations = 0;
  const selected: string[] = [];
  const firstSource = source([
    session("first"),
    session("unread", 1),
  ]);
  const secondSource = source([session("second")]);
  const render = async (sessionSource: SessionSidebarSessionSource, id: string) => {
    await React.act(async () => {
      root.render(
        <MemoryRouter>
          <ThemeProvider>
            <LobeUiProvider>
              <AuthProvider>
                <SessionSidebar
                  currentSessionId={id}
                  onSelectSession={(sessionId) => selected.push(sessionId)}
                  onNewSession={() => undefined}
                  sessionSource={sessionSource}
                  onThreadListAdapterChange={(adapter) => {
                    registrations += 1;
                    registered = adapter;
                  }}
                />
              </AuthProvider>
            </LobeUiProvider>
          </ThemeProvider>
        </MemoryRouter>,
      );
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
  };

  try {
    await render(firstSource, "first");
    const firstAdapter = registered;
    assert.ok(firstAdapter);
    assert.equal(firstAdapter.threadId, "first");
    assert.equal(firstAdapter.threads?.[0]?.id, "first");
    const row = [...container.querySelectorAll('[data-slot="thread-list-item"]')]
      .find((item) => item.textContent?.includes("Session unread"));
    assert.ok(row);
    const unreadLabel = row.querySelector(".sr-only")?.textContent?.trim();
    assert.ok(unreadLabel);

    await render(secondSource, "second");
    assert.ok(registered);
    assert.ok(registrations >= 2);
    await firstAdapter.onSwitchToThread?.("first");
    assert.deepEqual(selected, []);
    await registered.onSwitchToThread?.("second");
    assert.deepEqual(selected, ["second"]);

    await React.act(async () => root.unmount());
    assert.equal(registered, undefined);
  } finally {
    await React.act(async () => root.unmount());
    container.remove();
    authApi.bootstrapAuthContext = originalBootstrap;
    authApi.getCurrentUser = originalCurrentUser;
  }
});


test("global-history rename updates only the global source", async () => {
  const originalUpdate = sessionApi.update;
  const primaryUpdates: string[] = [];
  const globalUpdates: string[] = [];
  const primary = source([session("task")]);
  const global = source([session("global")]);
  primary.updateSession = (value) => primaryUpdates.push(value.id);
  global.updateSession = (value) => globalUpdates.push(value.id);
  sessionApi.update = async (id, _data) => ({
    status: "ok",
    session: session(id, 0),
  });

  const container = document.createElement("div");
  document.body.append(container);
  const root = await createLobeTestRoot(container);
  const click = async (element: Element | null) => {
    assert.ok(element);
    await React.act(async () => {
      element.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
  };
  try {
    await React.act(async () => {
      root.render(
        <MemoryRouter>
          <ThemeProvider>
            <LobeUiProvider>
              <AuthProvider>
                <SessionSidebar
                  currentSessionId="task"
                  onSelectSession={() => undefined}
                  onNewSession={() => undefined}
                  sessionSource={primary}
                  globalHistorySource={global}
                  agentWorkspace={{
                    agent_id: "general-agent",
                    name: "专家",
                    description: "",
                  }}
                  agentHistoryInMainPanel
                />
              </AuthProvider>
            </LobeUiProvider>
          </ThemeProvider>
        </MemoryRouter>,
      );
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
    const globalGroup = container.querySelector<HTMLElement>(
      "[data-workbench-global-history]",
    );
    assert.ok(globalGroup);
    const groupButton = globalGroup.querySelector<HTMLButtonElement>(
      "button[aria-expanded]",
    );
    assert.ok(groupButton);
    if (groupButton.getAttribute("aria-expanded") === "false") {
      await click(groupButton);
    }
    const row = [...globalGroup.querySelectorAll<HTMLElement>(
      '[data-slot="thread-list-item"]',
    )].find((item) => item.textContent?.includes("Session global"));
    assert.ok(row);
    await click(row.querySelectorAll("button")[1] ?? null);
    const renameItem = [...document.querySelectorAll<HTMLElement>(
      '[role="menuitem"]',
    )].find((item) => /重命名|Rename/.test(item.textContent ?? ""));
    await click(renameItem ?? null);
    const input = row.querySelector<HTMLInputElement>("input");
    assert.ok(input);
    await React.act(async () => {
      Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        "value",
      )!.set!.call(input, "Renamed global");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    assert.deepEqual(primaryUpdates, []);
    assert.deepEqual(globalUpdates, ["global", "global"]);
  } finally {
    await React.act(async () => root.unmount());
    container.remove();
    sessionApi.update = originalUpdate;
  }
});


test("delete receipts remain bound to the request owner and do not close a replacement confirmation", async () => {
  const originalDelete = sessionApi.delete;
  const firstDelete = deferred<unknown>();
  const secondDelete = deferred<unknown>();
  const deletes = [firstDelete, secondDelete];
  sessionApi.delete = async () => (await deletes.shift()!.promise) as never;
  const firstRemoved: string[] = [];
  const secondRemoved: string[] = [];
  const first = source([session("same")]);
  const second = source([session("same")]);
  first.removeSession = (id) => firstRemoved.push(id);
  second.removeSession = (id) => secondRemoved.push(id);
  const container = document.createElement("div");
  document.body.append(container);
  const root = await createLobeTestRoot(container);
  const click = async (element: Element | null) => {
    assert.ok(element);
    await React.act(async () => {
      element.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
  };
  const clickMenuItem = async (pattern: RegExp) => {
    const item = [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')]
      .find((candidate) => pattern.test(candidate.textContent ?? ""));
    await click(item ?? null);
  };
  const render = async (sessionSource: SessionSidebarSessionSource) => {
    await React.act(async () => {
      root.render(
        <MemoryRouter>
          <ThemeProvider>
            <LobeUiProvider>
              <AuthProvider>
                <SessionSidebar
                  currentSessionId="same"
                  onSelectSession={() => undefined}
                  onNewSession={() => undefined}
                  sessionSource={sessionSource}
                />
              </AuthProvider>
            </LobeUiProvider>
          </ThemeProvider>
        </MemoryRouter>,
      );
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
  };
  try {
    await render(first);
    const row = container.querySelector<HTMLElement>(
      '[data-slot="thread-list-item"]',
    );
    assert.ok(row);
    await click(row.querySelectorAll("button")[1] ?? null);
    await clickMenuItem(/删除|Delete/);
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]');
    assert.ok(dialog);
    const confirm = [...dialog.querySelectorAll<HTMLButtonElement>("button")]
      .find((candidate) => /删除|Delete/.test(candidate.textContent ?? ""));
    await click(confirm ?? null);

    await render(second);
    const replacementDialog = document.querySelector<HTMLElement>('[role="dialog"]');
    assert.ok(replacementDialog);
    const cancel = [...replacementDialog.querySelectorAll<HTMLButtonElement>("button")]
      .find((candidate) => /取消|Cancel/.test(candidate.textContent ?? ""));
    await click(cancel ?? null);
    const replacementRow = container.querySelector<HTMLElement>(
      '[data-slot="thread-list-item"]',
    );
    assert.ok(replacementRow);
    await click(replacementRow.querySelectorAll("button")[1] ?? null);
    await clickMenuItem(/删除|Delete/);
    const newDialog = document.querySelector<HTMLElement>('[role="dialog"]');
    assert.ok(newDialog);
    const newConfirm = [...newDialog.querySelectorAll<HTMLButtonElement>("button")]
      .find((candidate) => /删除|Delete/.test(candidate.textContent ?? ""));
    await click(newConfirm ?? null);
    await React.act(async () => {
      firstDelete.resolve({});
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    assert.deepEqual(firstRemoved, []);
    assert.deepEqual(secondRemoved, []);
    assert.ok(document.querySelector('[role="dialog"]'));
    await React.act(async () => {
      secondDelete.resolve({});
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
  } finally {
    await React.act(async () => root.unmount());
    container.remove();
    sessionApi.delete = originalDelete;
  }
});


test("delete A after selecting B and refreshing same-owner rows preserves B and removes A", async () => {
  const originalDelete = sessionApi.delete;
  const pending = deferred<unknown>();
  sessionApi.delete = async () => (await pending.promise) as never;
  const removed: string[] = [];
  let newSessionCalls = 0;
  const shared = source([session("a"), session("b")]);
  shared.removeSession = (id) => removed.push(id);
  const container = document.createElement("div");
  document.body.append(container);
  const root = await createLobeTestRoot(container);
  const click = async (element: Element | null) => {
    assert.ok(element);
    await React.act(async () => {
      element.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
  };
  const render = async (currentSessionId: string) => {
    await React.act(async () => {
      root.render(
        <MemoryRouter>
          <ThemeProvider>
            <LobeUiProvider>
              <AuthProvider>
                <SessionSidebar
                  currentSessionId={currentSessionId}
                  onSelectSession={() => undefined}
                  onNewSession={() => {
                    newSessionCalls += 1;
                  }}
                  sessionSource={shared}
                />
              </AuthProvider>
            </LobeUiProvider>
          </ThemeProvider>
        </MemoryRouter>,
      );
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
  };
  try {
    await render("a");
    const rowA = [...container.querySelectorAll<HTMLElement>(
      '[data-slot="thread-list-item"]',
    )].find((row) => row.textContent?.includes("Session a"));
    assert.ok(rowA);
    await click(rowA.querySelectorAll("button")[1] ?? null);
    const deleteItem = [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')]
      .find((item) => /删除|Delete/.test(item.textContent ?? ""));
    await click(deleteItem ?? null);
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]');
    assert.ok(dialog);
    const confirm = [...dialog.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => /删除|Delete/.test(button.textContent ?? ""));
    await click(confirm ?? null);

    await render("b");
    shared.sessions = [session("a"), session("b", 2), session("c")];
    await render("b");
    await React.act(async () => {
      pending.resolve({});
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    assert.deepEqual(removed, ["a"]);
    assert.equal(newSessionCalls, 0);
  } finally {
    await React.act(async () => root.unmount());
    container.remove();
    sessionApi.delete = originalDelete;
  }
});

test("SessionSidebar adapter publication does not loop through a parent state setter", async () => {
  const container = document.createElement("div");
  document.body.append(container);
  const root = await createLobeTestRoot(container);
  let renderCount = 0;
  let updateCount = 0;
  let latestAdapter: import("@assistant-ui/react").ExternalStoreThreadListAdapter | undefined;
  const selected: string[] = [];

  function Harness() {
    const [, updateAdapter] = React.useState<
      import("@assistant-ui/react").ExternalStoreThreadListAdapter | undefined
    >();
    const onAdapterChange = React.useCallback(
      (adapter: import("@assistant-ui/react").ExternalStoreThreadListAdapter | undefined) => {
        updateCount += 1;
        latestAdapter = adapter;
        updateAdapter(adapter);
      },
      [],
    );
    renderCount += 1;
    return (
      <MemoryRouter>
        <ThemeProvider>
          <LobeUiProvider>
            <AuthProvider>
              <SessionSidebar
                currentSessionId="stable"
                onSelectSession={() => selected.push("stable")}
                onNewSession={() => undefined}
                sessionSource={source([session("stable", 1)])}
                onThreadListAdapterChange={onAdapterChange}
              />
            </AuthProvider>
          </LobeUiProvider>
        </ThemeProvider>
      </MemoryRouter>
    );
  }

  try {
    await React.act(async () => {
      root.render(<Harness />);
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
    assert.ok(renderCount < 10, `unexpected parent rerender count: ${renderCount}`);
    assert.ok(updateCount < 10, `unexpected adapter update count: ${updateCount}`);
    await latestAdapter?.onSwitchToThread?.("stable");
    assert.deepEqual(selected, ["stable"]);
  } finally {
    await React.act(async () => root.unmount());
    container.remove();
  }
});