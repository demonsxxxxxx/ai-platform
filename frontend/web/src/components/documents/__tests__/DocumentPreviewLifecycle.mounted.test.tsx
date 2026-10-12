import assert from "node:assert/strict";
import test from "node:test";
// @ts-expect-error jsdom runtime import.
import { JSDOM } from "jsdom";
import type { ReactNode } from "react";

const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost/" });
const mediaListeners = new Set<(event: { matches: boolean }) => void>();
const frames = new Map<number, FrameRequestCallback>();
let nextFrame = 0;
Object.assign(globalThis, {
  window: dom.window, document: dom.window.document,
  localStorage: dom.window.localStorage, sessionStorage: dom.window.sessionStorage,
  Node: dom.window.Node, HTMLElement: dom.window.HTMLElement,
  ResizeObserver: class { observe() {} disconnect() {} },
  requestAnimationFrame: (callback: FrameRequestCallback) => { frames.set(++nextFrame, callback); return nextFrame; },
  cancelAnimationFrame: (id: number) => frames.delete(id),
  IS_REACT_ACT_ENVIRONMENT: true,
});
Object.defineProperty(globalThis, "navigator", { configurable: true, value: dom.window.navigator });
window.matchMedia = (() => ({
  matches: window.innerWidth < 640,
  addEventListener: (_type: string, callback: (event: { matches: boolean }) => void) => mediaListeners.add(callback),
  removeEventListener: (_type: string, callback: (event: { matches: boolean }) => void) => mediaListeners.delete(callback),
})) as unknown as typeof window.matchMedia;

const { act, createElement, StrictMode } = await import("react");
const { createRoot } = await import("react-dom/client");
const { default: i18n } = await import("i18next");
const { initReactI18next } = await import("react-i18next");
await i18n.use(initReactI18next).init({ lng: "en", resources: { en: { translation: {} } } });
// Mount the real document hook, toolbar, panel, portal, sidebar/swipe hooks and
// stores. jsdom supplies DOM APIs; no lifecycle or composition owner is mocked.
const { default: DocumentPreview } = await import("../DocumentPreview");
const { AttachmentPreviewHost } = await import("../../chat/AttachmentPreviewHost");
const { EditorSidebar } = await import("../../common/EditorSidebar");
const { ToolResultPanel } = await import("../../chat/ChatMessage/items/ToolResultPanel");
const attachment = await import("../../chat/attachmentPreviewStore");
const history = await import("../../chat/ChatMessage/items/sidebarHistoryStore");
const registry = await import("../../chat/ChatMessage/items/toolPanelRegistry");
const { claimChatPreviewSession } = await import("../../chat/chatPreviewSession");
const { clearAuthScopedCaches } = await import("../../../services/api/authCacheInvalidation");

function button(title: string): HTMLButtonElement {
  const key = ({ "Center view": "documents.centerView", "Sidebar view": "documents.sidebarView", Back: "common.back" } as Record<string, string>)[title] ?? title;
  const result = Array.from(document.querySelectorAll<HTMLButtonElement>("button")).find(node => (node.title === i18n.t(key) || node.getAttribute("aria-label") === i18n.t(key)));
  assert.ok(result, `Missing button: ${title}`); return result;
}
function overlay(): HTMLElement {
  const result = Array.from(document.body.children).find(node => node.classList.contains("fixed") && node.classList.contains("inset-0"));
  assert.ok(result, "Missing tool panel overlay"); return result as HTMLElement;
}
async function click(node: HTMLElement) { await act(async () => node.click()); }
async function escape() { await act(async () => { document.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true })); }); }
async function viewport(width: number) {
  await act(async () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
    mediaListeners.forEach(listener => listener({ matches: width < 640 }));
  });
}
function compressed(attribute = "data-sidebar-preview") { return document.documentElement.hasAttribute(attribute); }
async function harness() {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => new Response("# Synthetic preview\n\nLifecycle fixture.")) as typeof fetch;
  attachment.closeAttachmentPreview(); history.clearSidebarHistory(); registry.clearToolPanelRegistry();
  await viewport(1200);
  const container = document.createElement("div"); document.body.append(container);
  const root = createRoot(container);
  return {
    async render(children: ReactNode) {
      await act(async () => { root.render(children); });
      await act(async () => {
        for (let i = 0; i < 2; i++) {
          const pending = [...frames.values()]; frames.clear(); pending.forEach(callback => callback(0));
        }
      });
    },
    async open(id: string) {
      await act(async () => attachment.openAttachmentPreview({
        id, type: "document", key: id, name: `${id}.md`, size: 32, mimeType: "text/markdown", url: `/api/ai/files/${id}/preview`,
      }, "session-files"));
    },
    async close() {
      await act(async () => root.unmount()); container.remove();
      attachment.closeAttachmentPreview(); history.clearSidebarHistory(); registry.clearToolPanelRegistry();
      frames.clear(); globalThis.fetch = originalFetch;
      assert.equal(compressed(), false, "last unmount releases preview layout lease");
      assert.equal(compressed("data-editor-sidebar"), false, "last unmount releases editor layout lease");
    },
  };
}

for (const dismissal of ["X", "Escape", "overlay"] as const) {
  test(`mounted A→B→${dismissal}→C cannot restore A`, async () => {
    const view = await harness();
    try {
      await view.render(createElement(AttachmentPreviewHost)); await view.open("A"); await view.open("B");
      assert.equal(history.getSidebarHistoryLength(), 1);
      assert.equal(attachment.getAttachmentPreviewState()?.attachment.key, "B");
      if (dismissal === "X") await click(button("common.close"));
      else if (dismissal === "Escape") await escape();
      else { await click(button("Center view")); await click(overlay()); }
      assert.equal(attachment.getAttachmentPreviewState(), null);
      assert.equal(history.getSidebarHistoryLength(), 0); assert.equal(compressed(), false);
      await escape(); // Repeated dismissal after unmount is harmless.
      await view.open("C"); assert.equal(history.goBackSidebar(), false);
      assert.equal(attachment.getAttachmentPreviewState()?.attachment.key, "C");
      assert.equal(Array.from(document.querySelectorAll("button")).some(node => node.title === i18n.t("common.back")), false);
    } finally { await view.close(); }
  });
}

test("mounted Back and internal registry closure retain navigation history", async () => {
  const view = await harness();
  try {
    await view.render(createElement(AttachmentPreviewHost)); await view.open("A"); await view.open("B");
    await click(button("Back"));
    assert.equal(attachment.getAttachmentPreviewState()?.attachment.key, "A"); assert.equal(history.getSidebarHistoryLength(), 0);
    await view.open("B"); await act(async () => registry.closeCurrentToolPanel());
    assert.equal(attachment.getAttachmentPreviewState(), null);
    assert.equal(history.getSidebarHistoryLength(), 1, "internal close is not explicit user dismissal");
    await act(async () => { history.goBackSidebar(); });
    assert.equal(attachment.getAttachmentPreviewState()?.attachment.key, "A");
  } finally { await view.close(); }
});

test("document sidebar→center→fullscreen→sidebar releases only its desktop layout lease", async () => {
  const view = await harness();
  try {
    await view.render(createElement(AttachmentPreviewHost)); await view.open("A"); assert.equal(compressed(), true);
    const center = button("Center view"); center.focus(); await click(center);
    assert.equal(compressed(), false);
    assert.equal(document.activeElement, center, "mode toggle retains focus on the toolbar control");
    await click(button("documents.fullscreen")); assert.equal(compressed(), false);
    await click(button("Sidebar view")); assert.equal(compressed(), true);
    assert.ok(button("documents.fullscreen"), "returning to sidebar leaves fullscreen");
    await click(button("documents.fullscreen")); assert.equal(compressed(), false);
    await escape(); assert.equal(attachment.getAttachmentPreviewState(), null, "overlay retains Escape listener");
  } finally { await view.close(); }
});

test("desktop and mobile transitions keep close listeners without leaking layout leases", async () => {
  const view = await harness();
  try {
    await view.render(createElement(AttachmentPreviewHost)); await view.open("A");
    await viewport(500); assert.equal(compressed(), false); assert.equal(document.body.style.overflow, "hidden");
    await viewport(1200); assert.equal(compressed(), true); assert.equal(document.body.style.overflow, "");
    await click(button("Center view")); await viewport(500); await viewport(1200); assert.equal(compressed(), false);
    await escape(); assert.equal(attachment.getAttachmentPreviewState(), null);
  } finally { await view.close(); }
});

test("center and fullscreen preserve independent editor and simultaneous preview leases", async () => {
  const view = await harness(); let ownerClose = 0;
  const noop = () => { ownerClose++; };
  function tree(mode: "sidebar" | "center", fullscreen = false, first = true, second = true) {
    return createElement(StrictMode, null,
      first && createElement(DocumentPreview, { key: "document", path: "fixture.md", content: "fixture", registryKey: "shared-preview", onClose: noop }),
      second && createElement(ToolResultPanel, { key: "panel", open: true, onClose: noop, registryKey: "shared-preview", viewMode: mode, isFullscreen: fullscreen, children: "second preview" }),
      createElement(EditorSidebar, { key: "editor", open: true, onClose: noop, title: "Editor", children: "editor" }),
    );
  }
  try {
    await view.render(tree("sidebar")); assert.equal(ownerClose, 0, "same registry remounts never close each other");
    await click(button("Center view")); assert.equal(compressed(), true, "second sidebar retains shared attribute");
    await view.render(tree("center")); assert.equal(compressed(), false); assert.equal(compressed("data-editor-sidebar"), true);
    await view.render(tree("sidebar", true)); assert.equal(compressed(), false, "controlled fullscreen never reserves sidebar space");
    await view.render(tree("sidebar")); assert.equal(compressed(), true);
    await view.render(tree("sidebar", false, false)); assert.equal(compressed(), true, "older unmount cannot release newer lease");
    await view.render(tree("sidebar", false, false, false)); assert.equal(compressed(), false); assert.equal(compressed("data-editor-sidebar"), true);
    assert.equal(ownerClose, 0);
  } finally { await view.close(); }
});

test("registry replacement and stale unmount preserve new owner and existing ReactNode headers", async () => {
  const view = await harness(); let closesA = 0; let closesB = 0;
  const oldDocument = createElement(DocumentPreview, { key: "old", path: "old.md", content: "old", registryKey: "old", onClose: () => { closesA++; } });
  const newPanel = createElement(ToolResultPanel, { key: "new", open: true, registryKey: "new", onClose: () => { closesB++; }, children: "new", customHeader: createElement("div", null, "Existing ReactNode header") });
  try {
    await view.render(createElement(AttachmentPreviewHost)); await view.open("A"); await view.open("B");
    await view.render(oldDocument); await view.render([oldDocument, newPanel]);
    assert.equal(closesA, 1); assert.equal(closesB, 0); assert.equal(history.getSidebarHistoryLength(), 1);
    assert.match(document.body.textContent!, /Existing ReactNode header/);
    await view.render(newPanel); await act(async () => registry.closeCurrentToolPanel());
    assert.equal(closesB, 1, "stale cleanup cannot unregister replacement"); assert.equal(history.getSidebarHistoryLength(), 1);
  } finally { await view.close(); }
});

test("session and authentication revocation clear history independently of explicit close", async () => {
  const view = await harness();
  try {
    await act(async () => { claimChatPreviewSession("session-one"); });
    await view.render(createElement(AttachmentPreviewHost)); await view.open("A"); await view.open("B");
    await act(async () => { claimChatPreviewSession("session-one"); });
    assert.equal(history.getSidebarHistoryLength(), 1, "same-owner claim preserves history");
    await act(async () => { claimChatPreviewSession("session-two"); });
    assert.equal(history.getSidebarHistoryLength(), 0); assert.equal(attachment.getAttachmentPreviewState(), null);
    await view.open("C"); await view.open("D"); await act(async () => clearAuthScopedCaches());
    assert.equal(history.getSidebarHistoryLength(), 0); assert.equal(attachment.getAttachmentPreviewState(), null); assert.equal(compressed(), false);
  } finally { await view.close(); }
});

test("resize overlay guard and unmount cleanup survive layout lease separation", async () => {
  const view = await harness();
  try {
    await view.render(createElement(AttachmentPreviewHost)); await view.open("A"); await view.open("B");
    const handle = document.querySelector<HTMLElement>('[data-sidebar-panel] [class*="cursor-col-resize"]'); assert.ok(handle);
    await act(async () => { handle.dispatchEvent(new dom.window.MouseEvent("mousedown", { bubbles: true, clientX: 800 })); });
    assert.equal(document.body.style.cursor, "col-resize");
    await click(overlay()); assert.equal(attachment.getAttachmentPreviewState()?.attachment.key, "B"); assert.equal(history.getSidebarHistoryLength(), 1);
    await view.render(null); assert.equal(document.body.style.cursor, ""); assert.equal(document.body.style.userSelect, "");
    assert.equal(document.querySelector('[style*="z-index: 999999"]'), null); assert.equal(compressed(), false);
    assert.equal(history.getSidebarHistoryLength(), 1, "unmount does not become user dismissal");
  } finally { await view.close(); }
});

function touch(target: Element, type: string, clientY: number) {
  const event = new dom.window.Event(type, { bubbles: true, cancelable: true });
  Object.defineProperty(event, "touches", { value: [{ clientY }] });
  target.dispatchEvent(event);
}

test("mobile content swipe stays open; explicit handle swipe clears history", async () => {
  const view = await harness();
  try {
    await viewport(500); await view.render(createElement(AttachmentPreviewHost));
    await view.open("A"); await view.open("B");
    const content = document.querySelector("h3"); assert.ok(content);
    await act(async () => { touch(content, "touchstart", 0); touch(content, "touchmove", 150); touch(content, "touchend", 150); });
    assert.equal(attachment.getAttachmentPreviewState()?.attachment.key, "B");
    const handle = document.querySelector(".mobile-drag-handle"); assert.ok(handle);
    await act(async () => {
      touch(handle, "touchstart", 0); touch(handle, "touchmove", 150); touch(handle, "touchend", 150);
      await new Promise(resolve => setTimeout(resolve, 350));
    });
    assert.equal(attachment.getAttachmentPreviewState(), null); assert.equal(history.getSidebarHistoryLength(), 0);
    assert.equal(document.body.style.overflow, "");
  } finally { await view.close(); }
});

test("pending swipe cannot close a replacement document in the same mounted host", async () => {
  const view = await harness();
  try {
    await viewport(500); await view.render(createElement(AttachmentPreviewHost)); await view.open("A");
    const handle = document.querySelector(".mobile-drag-handle"); assert.ok(handle);
    await act(async () => { touch(handle, "touchstart", 0); touch(handle, "touchmove", 150); touch(handle, "touchend", 150); });
    await view.open("B");
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 350)); });
    assert.equal(attachment.getAttachmentPreviewState()?.attachment.key, "B");
    assert.equal(document.body.style.overflow, "hidden");
    const replacementHandle = document.querySelector(".mobile-drag-handle"); assert.ok(replacementHandle);
    await act(async () => {
      touch(replacementHandle, "touchstart", 0); touch(replacementHandle, "touchmove", 150); touch(replacementHandle, "touchend", 150);
      await new Promise(resolve => setTimeout(resolve, 350));
    });
    assert.equal(attachment.getAttachmentPreviewState(), null, "B retains its own swipe close");
    assert.equal(history.getSidebarHistoryLength(), 0);
  } finally { await view.close(); }
});

test("pending swipe cleanup and native fullscreen Escape cannot close a replacement document", async () => {
  const view = await harness();
  try {
    await viewport(500); await view.render(createElement(AttachmentPreviewHost)); await view.open("A");
    const handle = document.querySelector(".mobile-drag-handle"); assert.ok(handle);
    await act(async () => { touch(handle, "touchstart", 0); touch(handle, "touchmove", 150); touch(handle, "touchend", 150); });
    await view.render(null); await view.render(createElement(AttachmentPreviewHost)); await view.open("B");
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 350)); });
    assert.equal(attachment.getAttachmentPreviewState()?.attachment.key, "B");
    Object.defineProperty(document, "fullscreenElement", { configurable: true, value: document.body });
    await escape(); assert.equal(attachment.getAttachmentPreviewState()?.attachment.key, "B");
    Object.defineProperty(document, "fullscreenElement", { configurable: true, value: null });
    await escape(); assert.equal(attachment.getAttachmentPreviewState(), null);
  } finally {
    Object.defineProperty(document, "fullscreenElement", { configurable: true, value: null });
    await view.close();
  }
});

test("controlled open releases the lease before unmount and custom close notifies once in owner order", async () => {
  const view = await harness(); const events: string[] = [];
  const panel = (open: boolean) => createElement(ToolResultPanel, {
    open, onClose: () => events.push("close"), onUserClose: () => events.push("user"),
    customHeader: ({ onClose }) => createElement("button", { title: "custom-close", onClick: onClose }, "Close"),
    children: "panel",
  });
  try {
    await view.render(panel(true)); assert.equal(compressed(), true);
    const close = document.querySelector<HTMLButtonElement>('button[title="custom-close"]'); assert.ok(close);
    await click(close); assert.deepEqual(events, ["user", "close"]);
    await view.render(panel(false)); assert.equal(compressed(), false);
    await click(close); await escape(); assert.deepEqual(events, ["user", "close"], "closed panel no longer receives dismissal events");
    await view.render(panel(true)); assert.equal(compressed(), true);
    await escape(); assert.deepEqual(events, ["user", "close", "user", "close"]);
  } finally { await view.close(); }
});
