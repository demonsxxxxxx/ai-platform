import assert from "node:assert/strict";
import test, { after, beforeEach } from "node:test";
import type { ComponentProps, MouseEvent as ReactMouseEvent } from "react";
import { installSelectTestDom } from "../../common/__tests__/selectTestDom";
import { createLobeTestRoot } from "../../common/__tests__/lobeUiTestRoot";
import { installBrowserAuthTestDb } from "../../../hooks/__tests__/browserAuthTestDb";
import type { BackendSession } from "../../../services/api";
import type { SessionSidebarSessionSource } from "../SessionSidebar";

const dom = installSelectTestDom();
after(() => dom.close());
beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  installBrowserAuthTestDb();
});
Object.defineProperty(navigator, "locks", {
  configurable: true,
  value: {
    async request<T>(_name: string, _options: unknown, callback: () => Promise<T>) {
      return callback();
    },
  },
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
}
function session(id: string): BackendSession {
  return {
    id, name: `Synthetic session ${id}`, agent_id: "synthetic-agent",
    created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-02T00:00:00Z",
    is_active: true, metadata: {},
  };
}
function source(ids: string[]) {
  const removed: string[] = [];
  const updated: BackendSession[] = [];
  const value: SessionSidebarSessionSource = {
    sessions: ids.map(session), isLoading: false, isLoadingMore: false, hasMore: false,
    loadMore: async () => {}, softRefresh: async () => {}, prependSession: () => {},
    removeSession: (id) => { removed.push(id); }, updateSession: (item) => { updated.push(item); },
  };
  return { value, removed, updated };
}
function button(root: ParentNode, text: string): HTMLButtonElement {
  const node = Array.from(root.querySelectorAll("button")).find((candidate) => candidate.textContent?.trim() === text);
  assert.ok(node, `Missing button ${text}`);
  return node;
}
function clickCallback(node: HTMLElement) {
  const key = Object.keys(node).find((name) => name.startsWith("__reactProps$"));
  assert.ok(key, "The real mounted DOM node has React props");
  const props = (node as unknown as Record<string, unknown>)[key] as {
    onClick?: (event: ReactMouseEvent<HTMLElement>) => void;
  };
  assert.ok(props.onClick, "The real mounted control has a click handler");
  return () => props.onClick!(new MouseEvent("click", { bubbles: true }) as unknown as ReactMouseEvent<HTMLElement>);
}

async function mountSidebar(deleteRequest: (id: string) => Promise<Response>, initial = source(["a", "b"])) {
  const [React, { MemoryRouter }, { AuthProvider }, { SessionSidebar }, { authApi, sessionApi }, { toast }, { default: i18n }] = await Promise.all([
    import("react"), import("react-router-dom"), import("../../../hooks/useAuth"), import("../SessionSidebar"),
    import("../../../services/api"), import("@lobehub/ui/base-ui"), import("../../../i18n"),
  ]);
  const original = {
    bootstrap: authApi.bootstrapAuthContext, getUser: authApi.getCurrentUser, delete: sessionApi.delete,
    list: sessionApi.listAuthoritative, success: toast.success, error: toast.error, consoleError: console.error,
  };
  const successes: string[] = [];
  const errors: string[] = [];
  const diagnostics: unknown[][] = [];
  const selected: string[] = [];
  let newSessions = 0;
  authApi.bootstrapAuthContext = async (request) => ({ status: "ready", protocol_version: 2, generation: request.generation });
  authApi.getCurrentUser = async () => ({
    id: "synthetic-user", tenant_id: "synthetic-tenant", username: "Synthetic user", email: "synthetic@example.test",
    roles: [], permissions: [], is_active: true, is_admin: false, created_at: "", updated_at: "",
  });
  sessionApi.delete = deleteRequest;
  sessionApi.listAuthoritative = async () => { throw new Error("A supplied sessionSource must not fetch the default list"); };
  toast.success = (message) => { successes.push(String(message)); return { id: "success", close() {}, update() {} }; };
  toast.error = (message) => { errors.push(String(message)); return { id: "error", close() {}, update() {} }; };
  console.error = (...args) => {
    diagnostics.push(args);
    if (args[0] !== "Failed to delete session:") original.consoleError(...args);
  };
  const container = document.createElement("div");
  const root = await createLobeTestRoot(container);
  type SidebarProps = ComponentProps<typeof SessionSidebar>;
  let props: SidebarProps = {
    currentSessionId: "a", sessionSource: initial.value,
    onSelectSession: (id) => { selected.push(id); }, onNewSession: () => { newSessions += 1; },
    agentWorkspace: { agent_id: "synthetic-agent", name: "Synthetic agent", description: "Synthetic test fixture" },
  };
  let mounted = true;
  const tick = async (milliseconds = 0) => {
    await React.act(async () => { await new Promise((resolve) => setTimeout(resolve, milliseconds)); });
  };
  const render = async (changes: Partial<SidebarProps> = {}) => {
    props = { ...props, ...changes };
    await React.act(async () => {
      root.render(React.createElement(MemoryRouter, null,
        React.createElement(AuthProvider, null, React.createElement(SessionSidebar, props))));
    });
  };
  const dialog = () => {
    const node = document.querySelector<HTMLElement>('[role="dialog"]');
    assert.ok(node, "Delete confirmation is open");
    return node;
  };
  const click = async (node: HTMLElement) => {
    await React.act(async () => { node.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true })); });
  };
  const openMenu = async (id: string) => {
    const title = Array.from(container.querySelectorAll("div.truncate")).find((node) => node.textContent === `Synthetic session ${id}`);
    assert.ok(title, `Session ${id} is rendered by the real sidebar`);
    const row = title.closest(".group");
    assert.ok(row);
    const menuButton = row.querySelector<HTMLButtonElement>("button");
    assert.ok(menuButton);
    await click(menuButton);
    const item = Array.from(document.querySelectorAll<HTMLElement>('[role="menuitem"]')).find((node) => node.textContent?.trim() === i18n.t("common.delete"));
    assert.ok(item, "The real session menu exposes Delete");
    return item;
  };
  const openDelete = async (id: string) => {
    await click(await openMenu(id));
    dialog();
  };
  const escape = async () => {
    await React.act(async () => {
      (document.activeElement ?? dialog()).dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", code: "Escape", bubbles: true, cancelable: true }));
    });
  };
  const mask = async () => {
    // Focus guards sit between the popup and backdrop in the real Base UI portal.
    const backdrops = dialog().parentElement?.querySelectorAll<HTMLElement>(':scope > [role="presentation"]');
    const backdrop = backdrops?.[backdrops.length - 1];
    assert.ok(backdrop, "The real modal has a backdrop");
    await React.act(async () => {
      backdrop.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, pointerType: "mouse", button: 0 }));
      backdrop.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, button: 0 }));
      backdrop.dispatchEvent(new PointerEvent("pointerup", { bubbles: true, pointerType: "mouse", button: 0 }));
      backdrop.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, button: 0 }));
      backdrop.dispatchEvent(new MouseEvent("click", { bubbles: true, button: 0 }));
    });
  };
  const unmount = async () => {
    if (!mounted) return;
    mounted = false;
    await React.act(async () => root.unmount());
  };
  const close = async () => {
    await unmount();
    authApi.bootstrapAuthContext = original.bootstrap; authApi.getCurrentUser = original.getUser;
    sessionApi.delete = original.delete; sessionApi.listAuthoritative = original.list;
    toast.success = original.success; toast.error = original.error; console.error = original.consoleError;
  };
  try {
    await render();
    await tick();
    return {
      React, initial, successes, errors, diagnostics, selected, get newSessions() { return newSessions; },
      render, dialog, click, openMenu, openDelete, escape, mask, tick, unmount, close,
      confirm: () => button(dialog(), i18n.t("common.delete")),
      cancel: () => button(dialog(), i18n.t("common.cancel")),
      expectClosed: (reason = "Delete confirmation is closed") => assert.equal(document.querySelector('[role="dialog"]') === null, true, reason),
      deletedMessage: i18n.t("sidebar.sessionDeleted"), failedMessage: i18n.t("sidebar.deleteFailed"),
    };
  } catch (error) {
    await close();
    throw error;
  }
}

test("SessionSidebar deletion is single-flight even when the same mounted callback fires twice before React commits", async () => {
  const pending = deferred<Response>();
  const requests: string[] = [];
  const view = await mountSidebar((id) => { requests.push(id); return pending.promise; });
  try {
    await view.openDelete("a");
    const confirm = clickCallback(view.confirm());
    await view.React.act(async () => { confirm(); confirm(); });
    assert.deepEqual(requests, ["a"], "The synchronous owner lock prevents a duplicate DELETE");
    assert.equal(view.confirm().disabled, true);
    assert.equal(view.cancel().disabled, true);
    await view.React.act(async () => pending.resolve(new Response(null, { status: 204 })));
    await view.tick(300);
    assert.deepEqual(view.initial.removed, ["a"]);
    assert.equal(view.newSessions, 1);
    assert.deepEqual(view.successes, [view.deletedMessage]);
    view.expectClosed();
  } finally { await view.close(); }
});

test("pending deletion keeps the real confirmation locked against Cancel, Escape, mask and another row action", async () => {
  const pending = deferred<Response>();
  const requests: string[] = [];
  const view = await mountSidebar((id) => { requests.push(id); return pending.promise; });
  try {
    await view.openDelete("a");
    const staleCancel = clickCallback(view.cancel());
    const confirm = clickCallback(view.confirm());
    const otherRowDelete = await view.openMenu("b");
    await view.React.act(async () => {
      confirm();
      staleCancel();
      otherRowDelete.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    });
    assert.equal(view.confirm().disabled, true);
    assert.equal(view.cancel().disabled, true);
    await view.click(view.cancel());
    await view.tick(300);
    view.dialog();
    await view.escape();
    await view.tick(300);
    view.dialog();
    await view.mask();
    await view.tick(300);
    view.dialog();
    await view.openDelete("b");
    await view.React.act(async () => { confirm(); });
    assert.deepEqual(requests, ["a"]);
    assert.equal(view.confirm().disabled, true);
    await view.React.act(async () => pending.resolve(new Response(null, { status: 204 })));
    await view.tick(300);
    assert.deepEqual(view.initial.removed, ["a"], "Another row action cannot replace the pending target");
    assert.deepEqual(view.successes, [view.deletedMessage]);
    view.expectClosed();
  } finally { await view.close(); }
});

test("idle confirmation remains dismissible by Cancel, Escape and its real mask without deleting", async () => {
  const requests: string[] = [];
  const view = await mountSidebar(async (id) => { requests.push(id); return new Response(null, { status: 204 }); });
  try {
    for (const [label, dismiss] of [["Cancel", () => view.click(view.cancel())], ["Escape", view.escape], ["mask", view.mask]] as const) {
      await view.openDelete("a");
      assert.equal(view.cancel().disabled, false);
      await dismiss();
      await view.tick(300);
      view.expectClosed(`Idle ${label} dismisses the confirmation`);
    }
    assert.deepEqual(requests, []);
    assert.deepEqual(view.initial.removed, []);
    assert.equal(view.newSessions, 0);
    assert.deepEqual(view.successes, []);
    assert.deepEqual(view.errors, []);
  } finally { await view.close(); }
});

for (const outcome of ["success", "failure"] as const) {
  test(`a superseded source's late ${outcome} cannot close, unlock or mutate the next owner's pending dialog`, async () => {
    const first = deferred<Response>();
    const second = deferred<Response>();
    const requests: string[] = [];
    const next = source(["b", "c"]);
    const view = await mountSidebar((id) => { requests.push(id); return requests.length === 1 ? first.promise : second.promise; });
    try {
      await view.openDelete("a");
      await view.click(view.confirm());
      await view.render({ sessionSource: next.value, currentSessionId: "b" });
      await view.tick(300);
      view.expectClosed();
      await view.openDelete("b");
      assert.equal(view.confirm().disabled, false, "A new source must not inherit the previous pending lock");
      await view.click(view.confirm());
      assert.equal(view.confirm().disabled, true);
      await view.React.act(async () => {
        if (outcome === "success") first.resolve(new Response(null, { status: 204 }));
        else first.reject(new Error("Synthetic stale DELETE failure"));
      });
      await view.tick(300);
      assert.equal(view.confirm().disabled, true, "Old finalization cannot release the new request lock");
      assert.equal(view.cancel().disabled, true);
      assert.deepEqual(view.initial.removed, []);
      assert.deepEqual(next.removed, []);
      assert.deepEqual(next.updated, []);
      assert.equal(view.newSessions, 0);
      assert.deepEqual(view.successes, []);
      assert.deepEqual(view.errors, []);
      await view.click(view.confirm());
      assert.deepEqual(requests, ["a", "b"]);
      await view.React.act(async () => second.resolve(new Response(null, { status: 204 })));
      await view.tick(300);
      assert.deepEqual(next.removed, ["b"]);
      assert.deepEqual(view.initial.removed, []);
      assert.equal(view.newSessions, 1);
      assert.deepEqual(view.successes, [view.deletedMessage]);
      view.expectClosed();
    } finally { await view.close(); }
  });
}

test("returning to the same source callback creates a new deletion owner incarnation", async () => {
  const first = deferred<Response>();
  const second = deferred<Response>();
  const originalSource = source(["a", "b"]);
  const otherSource = source(["c"]);
  const requests: string[] = [];
  const view = await mountSidebar((id) => { requests.push(id); return requests.length === 1 ? first.promise : second.promise; }, originalSource);
  try {
    await view.openDelete("a");
    await view.click(view.confirm());
    await view.render({ sessionSource: otherSource.value, currentSessionId: "c" });
    await view.render({ sessionSource: originalSource.value, currentSessionId: "a" });
    await view.tick(300);
    view.expectClosed();
    await view.openDelete("a");
    await view.click(view.confirm());
    await view.React.act(async () => first.resolve(new Response(null, { status: 204 })));
    await view.tick(300);
    assert.equal(view.confirm().disabled, true);
    assert.deepEqual(originalSource.removed, []);
    assert.deepEqual(view.successes, []);
    assert.equal(view.newSessions, 0);
    await view.React.act(async () => second.resolve(new Response(null, { status: 204 })));
    await view.tick(300);
    assert.deepEqual(requests, ["a", "a"]);
    assert.deepEqual(originalSource.removed, ["a"]);
    assert.deepEqual(view.successes, [view.deletedMessage]);
    assert.equal(view.newSessions, 1);
    view.expectClosed();
  } finally { await view.close(); }
});

test("navigating to another current session while deleting does not clear the new selection", async () => {
  const pending = deferred<Response>();
  const requests: string[] = [];
  const view = await mountSidebar((id) => { requests.push(id); return pending.promise; });
  let clearedNewSelection = 0;
  try {
    await view.openDelete("a");
    await view.click(view.confirm());
    await view.render({ currentSessionId: "b", onNewSession: () => { clearedNewSelection += 1; } });
    assert.equal(view.confirm().disabled, true, "Navigation does not reset the current source's pending delete");
    await view.React.act(async () => pending.resolve(new Response(null, { status: 204 })));
    await view.tick(300);
    assert.deepEqual(requests, ["a"]);
    assert.deepEqual(view.initial.removed, ["a"]);
    assert.equal(view.newSessions, 0);
    assert.equal(clearedNewSelection, 0);
    assert.deepEqual(view.successes, [view.deletedMessage]);
    view.expectClosed();
  } finally { await view.close(); }
});

test("a current DELETE failure reports feedback, keeps the session and permits a fresh confirmed retry", async () => {
  const pending = deferred<Response>();
  const requests: string[] = [];
  const view = await mountSidebar((id) => {
    requests.push(id);
    return requests.length === 1 ? pending.promise : Promise.resolve(new Response(null, { status: 204 }));
  });
  try {
    await view.openDelete("a");
    await view.click(view.confirm());
    await view.React.act(async () => pending.reject(new Error("Synthetic current DELETE failure")));
    await view.tick(300);
    assert.deepEqual(view.errors, [view.failedMessage]);
    assert.deepEqual(view.successes, []);
    assert.deepEqual(view.initial.removed, []);
    assert.equal(view.newSessions, 0);
    assert.equal(view.diagnostics.filter((entry) => entry[0] === "Failed to delete session:").length, 1);
    view.expectClosed();
    await view.openDelete("a");
    assert.equal(view.confirm().disabled, false);
    assert.equal(view.cancel().disabled, false);
    await view.click(view.confirm());
    await view.tick(300);
    assert.deepEqual(requests, ["a", "a"]);
    assert.deepEqual(view.initial.removed, ["a"]);
    assert.deepEqual(view.successes, [view.deletedMessage]);
    assert.equal(view.newSessions, 1);
    view.expectClosed();
  } finally { await view.close(); }
});

for (const outcome of ["success", "failure"] as const) {
  test(`DELETE ${outcome} after unmount produces no list mutation, navigation or toast`, async () => {
    const pending = deferred<Response>();
    const view = await mountSidebar(() => pending.promise);
    try {
      await view.openDelete("a");
      await view.click(view.confirm());
      await view.unmount();
      await view.React.act(async () => {
        if (outcome === "success") pending.resolve(new Response(null, { status: 204 }));
        else pending.reject(new Error("Synthetic unmounted DELETE failure"));
      });
      assert.deepEqual(view.initial.removed, []);
      assert.deepEqual(view.initial.updated, []);
      assert.equal(view.newSessions, 0);
      assert.deepEqual(view.successes, []);
      assert.deepEqual(view.errors, []);
      assert.equal(view.diagnostics.filter((entry) => entry[0] === "Failed to delete session:").length, 0);
      view.expectClosed();
    } finally { await view.close(); }
  });
}
