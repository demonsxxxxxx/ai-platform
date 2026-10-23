import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import React from "react";
// @ts-expect-error jsdom is the pinned mounted-test runtime without declarations.
import { JSDOM } from "jsdom";
import { authApi } from "../../services/api/auth";
import { ThemeProvider, useTheme } from "../ThemeContext";
import { useSidebarPreference, SIDEBAR_COLLAPSED_STORAGE_KEY, SIDEBAR_MANAGEMENT_COLLAPSED_STORAGE_KEY } from "../../hooks/useSidebarPreference";
import { LEGACY_THEME_STORAGE_KEY, THEME_STORAGE_KEY } from "../../utils/themeDom";

async function withPreferences(
  stored: Record<string, string>,
  run: (view: {
    theme: () => ReturnType<typeof useTheme>;
    sidebar: () => ReturnType<typeof useSidebarPreference>;
    management: () => ReturnType<typeof useSidebarPreference>;
    os: (dark: boolean) => Promise<void>;
    act: (action: () => void) => Promise<void>;
    remount: () => Promise<void>;
    requests: string[];
    storage: Storage;
    listeners: Set<(event: { matches: boolean }) => void>;
  }) => Promise<void>,
) {
  const dom = new JSDOM("<!doctype html><div id='root'></div>", { url: "https://synthetic.test" });
  const globals = { window: dom.window, document: dom.window.document, localStorage: dom.window.localStorage, CustomEvent: dom.window.CustomEvent, navigator: dom.window.navigator, IS_REACT_ACT_ENVIRONMENT: true };
  const previous = new Map(Object.keys(globals).map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  for (const [key, value] of Object.entries(globals)) Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
  for (const [key, value] of Object.entries(stored)) localStorage.setItem(key, value);
  let dark = false;
  const listeners = new Set<(event: { matches: boolean }) => void>();
  dom.window.matchMedia = (() => ({ get matches() { return dark; }, addEventListener(_name: string, cb: (event: { matches: boolean }) => void) { listeners.add(cb); }, removeEventListener(_name: string, cb: (event: { matches: boolean }) => void) { listeners.delete(cb); } })) as never;
  const originalFetch = globalThis.fetch;
  const requests: string[] = [];
  globalThis.fetch = async (url) => {
    requests.push(String(url));
    return new Response(JSON.stringify({ user_id: "synthetic-user", display_name: "Synthetic", tenant_id: "synthetic-tenant", department_id: "synthetic-department", roles: [], permissions: [], is_admin: false, source: "company", metadata: { theme: "dark", sidebarCollapsed: "true", defaultThinkingLevel: "high" } }), { headers: { "Content-Type": "application/json" } });
  };
  const { createRoot } = await import("react-dom/client");
  let root = createRoot(document.getElementById("root")!);
  let theme!: ReturnType<typeof useTheme>;
  let sidebar!: ReturnType<typeof useSidebarPreference>;
  let management!: ReturnType<typeof useSidebarPreference>;
  function Probe() { theme = useTheme(); sidebar = useSidebarPreference(); management = useSidebarPreference(SIDEBAR_MANAGEMENT_COLLAPSED_STORAGE_KEY); return null; }
  const render = async () => { await React.act(async () => root.render(<React.StrictMode><ThemeProvider><Probe /></ThemeProvider></React.StrictMode>)); };
  const act = async (action: () => void) => { await React.act(async () => action()); };
  try {
    await render();
    await run({ theme: () => theme, sidebar: () => sidebar, management: () => management, act, storage: localStorage, listeners, requests,
      os: async (value) => { dark = value; await act(() => listeners.forEach(listener => listener({ matches: dark }))); },
      remount: async () => { await act(() => root.unmount()); assert.equal(listeners.size, 0); root = createRoot(document.getElementById("root")!); await render(); },
    });
  } finally {
    await act(() => root.unmount());
    assert.equal(listeners.size, 0, "unmount releases the OS listener");
    globalThis.fetch = originalFetch;
    for (const [key, descriptor] of previous) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else Reflect.deleteProperty(globalThis, key); }
    dom.window.close();
  }
}

test("OS-derived theme stays transient until an explicit choice; no account requests", async () => {
  await withPreferences({}, async ({ theme, sidebar, os, act, storage, requests, remount, listeners }) => {
    assert.equal(theme().theme, "light");
    assert.equal(storage.getItem(THEME_STORAGE_KEY), null);
    assert.equal(sidebar()[0], false);
    assert.equal(storage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY), null);
    assert.equal(listeners.size, 1);
    await os(true);
    assert.equal(theme().theme, "dark");
    assert.equal(document.documentElement.classList.contains("dark"), true);
    assert.equal(storage.getItem(THEME_STORAGE_KEY), null);
    await act(() => theme().toggleTheme());
    assert.equal(theme().theme, "light");
    assert.equal(storage.getItem(THEME_STORAGE_KEY), "light");
    await os(true);
    assert.equal(theme().theme, "light");
    await act(() => { sidebar()[1](previous => !previous); sidebar()[1](previous => !previous); });
    assert.equal(sidebar()[0], false);
    await act(() => sidebar()[1](true));
    assert.equal(storage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY), "true");
    await remount();
    assert.equal(theme().theme, "light");
    assert.equal(sidebar()[0], true);
    assert.deepEqual(requests, []);
  });
});

test("management and shell collapse choices persist independently without default writes", async () => {
  await withPreferences({}, async ({ sidebar, management, act, storage, remount }) => {
    assert.equal(management()[0], false, "management defaults expanded");
    assert.equal(storage.getItem(SIDEBAR_MANAGEMENT_COLLAPSED_STORAGE_KEY), null);
    await act(() => management()[1](true));
    await remount();
    assert.equal(management()[0], true);
    assert.equal(sidebar()[0], false);
    await act(() => { management()[1](false); sidebar()[1](true); });
    await remount();
    assert.equal(management()[0], false);
    assert.equal(sidebar()[0], true);
  });
});

test("stored and legacy theme choices retain precedence without bootstrap writes", async () => {
  for (const key of [THEME_STORAGE_KEY, LEGACY_THEME_STORAGE_KEY]) {
    await withPreferences({ [key]: "dark" }, async ({ theme, os, storage, requests }) => {
      assert.equal(theme().theme, "dark");
      await os(false);
      assert.equal(theme().theme, "dark");
      assert.equal(storage.getItem(key), "dark");
      if (key === LEGACY_THEME_STORAGE_KEY) assert.equal(storage.getItem(THEME_STORAGE_KEY), null);
      assert.deepEqual(requests, []);
    });
  }
});

test("real principal mapping does not restore profile preferences or write them back", async () => {
  await withPreferences({}, async ({ theme, sidebar, act, requests }) => {
    const user = await authApi.getCurrentUser();
    assert.deepEqual(user.metadata, { display_name: "Synthetic", source: "company" });
    await act(() => {
      window.dispatchEvent(new CustomEvent("theme:external-change", { detail: "dark" }));
      window.dispatchEvent(new CustomEvent("sidebar-collapsed-changed", { detail: true }));
    });
    assert.equal(theme().theme, "light");
    assert.equal(sidebar()[0], false);
    assert.deepEqual(requests, ["/api/ai/auth/me"]);
  });
});

test("blocked storage does not prevent explicit local theme or sidebar changes", async () => {
  await withPreferences({}, async ({ theme, sidebar, management, os, act, storage, requests }) => {
    const prototype = Object.getPrototypeOf(storage) as Storage;
    const original = prototype.setItem;
    prototype.setItem = () => { throw new DOMException("blocked", "SecurityError"); };
    try {
      await act(() => { theme().setTheme("dark"); sidebar()[1](true); management()[1](true); });
      await os(false);
      assert.equal(theme().theme, "dark");
      assert.equal(sidebar()[0], true);
      assert.equal(management()[0], true);
      assert.deepEqual(requests, []);
    } finally {
      prototype.setItem = original;
    }
  });
});

test("all four sidebar writers use the local owner and auth has no dead preference bridge", () => {
  for (const file of ["components/layout/AppContent/index.tsx", "features/agent-builder/AgentBuilderShell.tsx", "features/agent-market/AgentMarketRoute.tsx", "features/agent-market/AgentWorkspaceRoute.tsx"]) {
    const source = readFileSync(new URL(`../../${file}`, import.meta.url), "utf8");
    assert.match(source, /useSidebarPreference\(\)/, file);
    assert.doesNotMatch(source, /updateMetadata|sidebar-collapsed-changed/, file);
  }
  const auth = readFileSync(new URL("../../hooks/useAuth.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(auth, /applyUserMetadata|theme:external-change|thinking-preference-updated|sidebar-collapsed-changed/);
  const options = readFileSync(new URL("../../components/layout/AppContent/useAgentOptions.ts", import.meta.url), "utf8");
  assert.match(options, /applyStoredAgentOptionDefaults/);
  assert.doesNotMatch(options, /thinking-preference-updated/);
});
