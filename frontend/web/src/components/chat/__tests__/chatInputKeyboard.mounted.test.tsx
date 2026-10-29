import assert from "node:assert/strict";
import test from "node:test";
import { installSelectTestDom } from "../../common/__tests__/selectTestDom";
import { act } from "react";
import { ThreadPrimitive } from "@assistant-ui/react";
import { installBrowserAuthTestDb } from "../../../hooks/__tests__/browserAuthTestDb";
import type { ChatInputProps } from "../chatInputTypes";
import type { SubmissionOutcome } from "../../../hooks/useAgent/types";

function installDom() {
  const fixture = installSelectTestDom();
  document.body.innerHTML = '<div id="root"></div>';
  Object.assign(globalThis, { localStorage: window.localStorage, sessionStorage: window.sessionStorage });
  return { window, close: fixture.close };
}

// Mounted DOM-event contracts only: jsdom does not run a real IME or native caret movement.
test("composer preserves IME, multiline editing, history, menu and submission ownership", async (t) => {
  const dom = installDom();
  installBrowserAuthTestDb();
  Object.defineProperty(navigator, "locks", { configurable: true, value: {
    request: async (_name: string, _options: unknown, callback: () => Promise<unknown>) => callback(),
  } });
  const { createLobeTestRoot } = await import("../../common/__tests__/lobeUiTestRoot");
  const { ChatInput } = await import("../ChatInput");
  const { AssistantUiTestProvider } = await import("../assistant-ui/__tests__/assistantUiTestProvider");
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
  const root = await createLobeTestRoot(container);
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
    await act(async () => { root.render(<AuthProvider><AssistantUiTestProvider isRunning={props.isLoading}><ThreadPrimitive.Suggestion prompt="建议问题" send={false} data-test-suggestion>建议问题</ThreadPrimitive.Suggestion><ChatInput {...props} /></AssistantUiTestProvider></AuthProvider>); });
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
  const dialogOpen = () => document.querySelector('[role="dialog"], [role="alertdialog"]') !== null;
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
      await act(async () => { await new Promise((resolve) => setTimeout(resolve, 300)); });
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
    await t.test("opening the model picker refreshes once, keeps drafts, and recovers an empty list", async () => {
      let refreshCalls = 0;
      await render({ disableSlashCommands: false, availableModels: [],
        onSelectModel: () => undefined, onModelSelectorOpen: () => { refreshCalls += 1; } });
      await type("保留模型刷新草稿");
      const trigger = () => container.querySelector<HTMLButtonElement>("[data-composer-model-trigger]")!;
      assert.ok(trigger(), "empty catalogs keep the retry entry visible");
      await act(async () => trigger().click());
      assert.equal(refreshCalls, 1);
      assert.ok(document.querySelector('[data-composer-model-panel]'));
      assert.equal(textarea().value, "保留模型刷新草稿");
      await render({ availableModels: [{ id: "model-b", value: "model-b", label: "Model B" }] });
      assert.equal(refreshCalls, 1, "catalog updates do not reopen the picker");
      assert.ok(document.querySelector('[data-composer-model-panel]'));
      assert.equal(textarea().value, "保留模型刷新草稿");
      await act(async () => {
        trigger().dispatchEvent(new dom.window.MouseEvent("mousedown", { bubbles: true, button: 0 }));
      });
      await act(async () => trigger().click());
      assert.equal(document.querySelector('[data-composer-model-panel]'), null, "second pointer click closes the model menu");
      assert.equal(trigger().getAttribute("aria-expanded"), "false");
      assert.equal(refreshCalls, 1, "closing the picker does not refresh the catalog");
      await type("/model");
      assert.equal(refreshCalls, 2, "slash command opens the same refresh seam");
      await type("/model B");
      assert.equal(refreshCalls, 2, "editing search must not request another catalog");
      await key("Escape");
      await type("/");
      for (let i = 0; i < 8 && container.querySelector('[role="option"][aria-selected="true"]')?.getAttribute("data-composer-command-item") !== "model"; i += 1) {
        await key("ArrowDown");
      }
      assert.equal(container.querySelector('[role="option"][aria-selected="true"]')?.getAttribute("data-composer-command-item"), "model");
      await key("Enter");
      assert.equal(refreshCalls, 3, "slash menu selection refreshes as well");
      await key("Escape");
      await render({ onSelectModel: undefined, onModelSelectorOpen: undefined, availableModels: [] });
    });
    await t.test("unavailable model blocks sending but preserves the editable draft and input identity", async () => {
      const reason = "尚未分配可用模型，请联系管理员。";
      const before = sends.length;
      await render({ modelUnavailableReason: reason, onSelectModel: () => undefined });
      await type("等待模型配置的草稿");
      const editor = textarea();
      const send = container.querySelector<HTMLButtonElement>('[data-slot="composer-send"]')!;
      assert.equal(editor.disabled, false);
      assert.equal(send.disabled, true);
      assert.match(container.querySelector('[data-composer-model-trigger]')?.textContent ?? "", /暂无可用模型/);
      assert.equal(container.querySelector('[data-composer-model-status]')?.textContent?.includes(reason), true);
      assert.equal(container.textContent?.includes("配置模型组"), false);
      await key("Enter");
      await act(async () => send.click());
      assert.equal(sends.length, before);
      assert.equal(editor.value, "等待模型配置的草稿");
      await render({ modelUnavailableReason: undefined,
        onSelectModel: undefined, availableModels: [] });
      assert.equal(textarea(), editor);
      assert.equal(editor.value, "等待模型配置的草稿");
      assert.equal(container.querySelector<HTMLButtonElement>('[data-slot="composer-send"]')?.disabled, false);
    });
    await t.test("native suggestions, send and permission/upload gates use the platform payload", async () => {
      const submitted: Parameters<ChatInputProps["onSend"]>[] = [];
      let accepted = false;
      const attachment = { id: "attachment-public", key: "attachment-public", name: "report.txt", type: "document" as const, url: "/api/files/report", mimeType: "text/plain", size: 20 };
      await render({ disableSlashCommands: true, attachments: [attachment], agentOptionValues: { model: "selected-model" },
        onSend: async (...args) => { submitted.push(args); return accepted ? { status: "accepted" } : { status: "failed" }; } });
      await act(async () => container.querySelector<HTMLButtonElement>("[data-test-suggestion]")!.click());
      assert.equal(textarea().value, "建议问题");
      assert.equal(submitted.length, 0);
      await render({ canSend: false });
      await key("Enter");
      assert.equal(submitted.length, 0);
      await render({ canSend: true, attachments: [{ ...attachment, isUploading: true }] });
      await key("Enter");
      assert.equal(submitted.length, 0);
      await render({ attachments: [attachment] });
      const send = () => container.querySelector<HTMLButtonElement>("button[aria-label='发送消息']")!;
      await act(async () => send().click());
      assert.equal(submitted.length, 1);
      assert.deepEqual(submitted[0], ["建议问题", { model: "selected-model" }, [attachment], null]);
      assert.equal(textarea().value, "建议问题", "unaccepted submission retains the durable draft");
      assert.equal(send().disabled, false, "native send remains usable after an unaccepted submission");
      accepted = true;
      await act(async () => send().click());
      assert.equal(submitted.length, 2);
      assert.equal(textarea().value, "");
    });
    await t.test("question mode preserves the ordinary draft and attachments while blocking hidden submission and drops", async () => {
      const attachment = { id: "draft-file", key: "draft-file", name: "draft.txt", type: "document" as const, url: "/api/files/draft", mimeType: "text/plain", size: 5 };
      let sent = 0;
      let imported = 0;
      let stops = 0;
      await render({ attachments: [attachment], isLoading: false, canSend: true,
        draftScopeKey: "question-session", onSend: async () => { sent += 1; return { status: "accepted" }; },
        onProfileDriveFileDrop: async () => { imported += 1; },
        onStop: async () => { stops += 1; return "acknowledged"; } });
      await type("保留普通草稿");
      const originalInput = textarea();
      const originalFile = container.querySelector("[data-composer-file-reference]");
      assert.ok(originalFile);
      await render({ questionMode: true, taskSurface: (stop) => <section data-test-question>当前问题{stop}</section> });
      assert.equal(textarea(), originalInput, "question mode must not remount the input owner");
      assert.equal(originalInput.value, "保留普通草稿");
      assert.equal(originalInput.disabled, true);
      assert.equal(container.querySelector<HTMLElement>('[data-slot="composer-ordinary-controls"]')!.style.display, "none");
      await key("Enter");
      await act(async () => originalInput.closest("form")!.requestSubmit());
      assert.equal(sent, 0, "even explicit hidden-form submission is blocked");
      const bar = container.querySelector('[data-slot="composer-bar"]')!;
      const drop = new dom.window.Event("drop", { bubbles: true, cancelable: true });
      Object.defineProperty(drop, "dataTransfer", { value: {
        files: [new File(["new"], "new.txt", { type: "text/plain" })],
        types: ["application/x-ai-platform-profile-drive-path", "Files"],
        getData: () => JSON.stringify({ source_id: "profile", path: "synthetic.txt" }),
      } });
      await act(async () => bar.dispatchEvent(drop));
      assert.equal(imported, 0, "question drops cannot mutate hidden file references");
      assert.equal(container.querySelector("[data-composer-file-reference]"), originalFile);
      await render({ isLoading: true, canStop: true });
      await act(async () => container.querySelector<HTMLButtonElement>("[data-run-question-stop]")!.click());
      const dialog = document.querySelector('[role="dialog"], [role="alertdialog"]');
      assert.ok(dialog, "question stop keeps the existing confirmation");
      assert.equal(stops, 0);
      const confirm = [...dialog.querySelectorAll("button")].find((button) => button.textContent?.includes("停止"));
      assert.ok(confirm);
      await act(async () => confirm.click());
      assert.equal(stops, 1);
      await render({ questionMode: false, taskSurface: undefined, isLoading: false });
      assert.equal(textarea(), originalInput);
      assert.equal(textarea().value, "保留普通草稿");
      assert.equal(container.querySelector("[data-composer-file-reference]"), originalFile);
      await render({ questionMode: true, draftScopeKey: "different-session" });
      assert.equal(textarea().value, "", "scope changes still reset the hidden draft");
      await render({ questionMode: false });
    });
  } finally {
    await act(async () => root.unmount());
    authApi.bootstrapAuthContext = originalBootstrap;
    authApi.getCurrentUser = originalUser;
    dom.window.close();
  }
});
