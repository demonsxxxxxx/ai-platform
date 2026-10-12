import assert from "node:assert/strict";
import test from "node:test";
// @ts-expect-error jsdom is the pinned mounted-test runtime without declarations.
import { JSDOM } from "jsdom";
import { act } from "react";
import { installBrowserAuthTestDb } from "../../../hooks/__tests__/browserAuthTestDb";
import type { ChatInputProps } from "../chatInputTypes";
import type { SubmissionOutcome } from "../../../hooks/useAgent/types";

function installDom() {
  const dom = new JSDOM("<!doctype html><html><body><div id=\"root\"></div></body></html>", {
    url: "http://localhost/",
  });
  const window = dom.window;
  Object.defineProperties(globalThis, {
    window: { configurable: true, value: window },
    document: { configurable: true, value: window.document },
    navigator: { configurable: true, value: window.navigator },
    HTMLElement: { configurable: true, value: window.HTMLElement },
    Element: { configurable: true, value: window.Element },
    Node: { configurable: true, value: window.Node },
    HTMLTextAreaElement: {
      configurable: true,
      value: window.HTMLTextAreaElement,
    },
    Event: { configurable: true, value: window.Event },
    InputEvent: { configurable: true, value: window.InputEvent },
    CustomEvent: { configurable: true, value: window.CustomEvent },
    localStorage: { configurable: true, value: window.localStorage },
    sessionStorage: { configurable: true, value: window.sessionStorage },
    requestAnimationFrame: {
      configurable: true,
      value: (callback: FrameRequestCallback) =>
        window.setTimeout(() => callback(Date.now()), 0),
    },
    cancelAnimationFrame: {
      configurable: true,
      value: (handle: number) => window.clearTimeout(handle),
    },
    IS_REACT_ACT_ENVIRONMENT: { configurable: true, value: true },
  });
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: () => ({
      matches: false,
      media: "",
      onchange: null,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
      dispatchEvent: () => false,
    }),
  });
  return dom;
}

// Mounted DOM-event contracts only: jsdom does not run a real IME or native caret movement.
test("composer preserves IME, multiline editing, history, menu and submission ownership", async (t) => {
  const dom = installDom();
  installBrowserAuthTestDb();
  Object.defineProperty(navigator, "locks", { configurable: true, value: {
    request: async (_name: string, _options: unknown, callback: () => Promise<unknown>) => callback(),
  } });
  const { createRoot } = await import("react-dom/client");
  const { ChatInput } = await import("../ChatInput");
  const { AuthProvider } = await import("../../../hooks/useAuth");
  const { authApi } = await import("../../../services/api/auth");
  const originalBootstrap = authApi.bootstrapAuthContext;
  const originalUser = authApi.getCurrentUser;
  authApi.bootstrapAuthContext = async (request) => ({ status: "ready", protocol_version: 2, generation: request.generation });
  authApi.getCurrentUser = async () => ({
    id: "keyboard-user", tenant_id: "keyboard-tenant", username: "keyboard-user",
    email: "keyboard@example.test", roles: [], permissions: [], is_active: true,
    created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
  });
  const container = document.getElementById("root")!;
  const root = createRoot(container);
  const sends: string[] = [];
  let resolveSubmission: ((outcome: SubmissionOutcome) => void) | undefined;
  let deferSubmission = false;
  const props: ChatInputProps = {
    onSend: async (value) => {
      sends.push(value);
      if (deferSubmission) return new Promise<SubmissionOutcome>((resolve) => { resolveSubmission = resolve; });
      return { status: "accepted" };
    },
    onStop: async () => "unavailable", isLoading: false,
    attachments: [], acceptedFileTypes: [], disableSlashCommands: true,
    tools: [], skills: [], enableSkills: false, agentOptionValues: {}, availableModels: [],
  };
  const render = async (changes: Partial<ChatInputProps> = {}) => {
    Object.assign(props, changes);
    await act(async () => { root.render(<AuthProvider><ChatInput {...props} /></AuthProvider>); });
  };
  const textarea = () => container.querySelector("textarea")!;
  const type = async (value: string) => {
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(textarea(), value);
      textarea().dispatchEvent(new dom.window.InputEvent("input", { bubbles: true, data: value, inputType: "insertText" }));
    });
  };
  const key = async (name: string, options: KeyboardEventInit = {}) => {
    const event = new dom.window.KeyboardEvent("keydown", { key: name, bubbles: true, cancelable: true, ...options });
    await act(async () => { textarea().dispatchEvent(event); });
    return event;
  };
  const caret = (start: number, end = start) => textarea().setSelectionRange(start, end);
  const dialogOpen = () => document.body.querySelector(".enterprise-modal-shell") !== null;
  try {
    await render();
    await t.test("empty history leaves arrow keys native", async () => {
      await type("");
      assert.equal((await key("ArrowUp")).defaultPrevented, false);
      assert.equal((await key("ArrowDown")).defaultPrevented, false);
    });
    await t.test("IME Enter stays native for composing and legacy 229 signals", async () => {
      await type("中文候选");
      assert.equal((await key("Enter", { isComposing: true })).defaultPrevented, false);
      assert.equal((await key("Enter", { keyCode: 229 })).defaultPrevented, false);
      assert.deepEqual(sends, []);
      assert.equal(textarea().value, "中文候选");
    });
    await t.test("Enter submits once while pending; newline modifiers stay native", async () => {
      await type("first history item");
      assert.equal((await key("Enter", { shiftKey: true })).defaultPrevented, false);
      localStorage.setItem("newlineModifier", "ctrl");
      assert.equal((await key("Enter", { ctrlKey: true })).defaultPrevented, false);
      localStorage.removeItem("newlineModifier");
      deferSubmission = true;
      assert.equal((await key("Enter")).defaultPrevented, true);
      await key("Enter");
      assert.equal(sends.length, 1);
      await act(async () => resolveSubmission!({ status: "accepted" }));
      deferSubmission = false;
      assert.equal(textarea().value, "");
    });
    await t.test("ArrowDown does not swallow normal multiline editing with saved history", async () => {
      await type("first line\nsecond line");
      caret(3);
      assert.equal((await key("ArrowDown")).defaultPrevented, false);
      assert.equal(textarea().value, "first line\nsecond line");
      caret(textarea().value.length);
      assert.equal((await key("ArrowDown")).defaultPrevented, false);
      caret(1, 4);
      assert.equal((await key("ArrowUp")).defaultPrevented, false);
      assert.equal((await key("ArrowDown")).defaultPrevented, false);
    });
    await t.test("history boundary navigation restores draft and does not consume IME arrows", async () => {
      await type("draft\nsecond line");
      caret(0);
      assert.equal((await key("ArrowUp", { isComposing: true })).defaultPrevented, false);
      assert.equal(textarea().value, "draft\nsecond line");
      assert.equal((await key("ArrowUp")).defaultPrevented, true);
      assert.equal(textarea().value, "first history item");
      caret(3);
      assert.equal((await key("ArrowDown")).defaultPrevented, false);
      caret(textarea().value.length);
      assert.equal((await key("ArrowDown", { isComposing: true })).defaultPrevented, false);
      assert.equal((await key("ArrowDown")).defaultPrevented, true);
      assert.equal(textarea().value, "draft\nsecond line");
      caret(textarea().value.length);
      assert.equal((await key("ArrowDown")).defaultPrevented, false);
    });
    await t.test("busy IME Enter never opens stop confirmation; ordinary Enter still does", async () => {
      await render({ isLoading: true, canStop: true });
      assert.equal((await key("Enter", { isComposing: true })).defaultPrevented, false);
      assert.equal((await key("Enter", { keyCode: 229 })).defaultPrevented, false);
      assert.equal(dialogOpen(), false);
      assert.equal((await key("Enter")).defaultPrevented, true);
      assert.equal(dialogOpen(), true);
      await act(async () => document.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
      await render({ canStop: false });
      await key("Enter");
      assert.equal(dialogOpen(), false);
      assert.equal(sends.length, 1);
      await render({ isLoading: false });
    });
    await t.test("slash menu retains arrows, Tab and Escape without consuming IME keys", async () => {
      await render({ disableSlashCommands: false });
      await type("/");
      const selectedCommand = () => container.querySelector('[role="option"][aria-selected="true"]')?.getAttribute("data-composer-command-item");
      assert.equal(selectedCommand(), "skill");
      assert.equal((await key("Enter", { keyCode: 229 })).defaultPrevented, false);
      for (const name of ["Enter", "ArrowDown", "ArrowUp", "Tab", "Escape"]) {
        assert.equal((await key(name, { isComposing: true })).defaultPrevented, false);
        assert.equal(textarea().value, "/");
        assert.equal(selectedCommand(), "skill");
      }
      assert.equal((await key("ArrowDown")).defaultPrevented, true);
      assert.equal(selectedCommand(), "mcp");
      assert.equal((await key("ArrowUp")).defaultPrevented, true);
      assert.equal(selectedCommand(), "skill");
      assert.equal((await key("Tab")).defaultPrevented, true);
      assert.equal(selectedCommand(), undefined);
      assert.equal(sends.length, 1);
      await type("/");
      assert.equal((await key("Enter")).defaultPrevented, true);
      assert.equal(selectedCommand(), undefined);
      assert.equal(sends.length, 1);
      await type("/");
      assert.equal((await key("Escape")).defaultPrevented, true);
      assert.equal((await key("Escape")).defaultPrevented, false);
    });
  } finally {
    await act(async () => root.unmount());
    authApi.bootstrapAuthContext = originalBootstrap;
    authApi.getCurrentUser = originalUser;
    dom.window.close();
  }
});
