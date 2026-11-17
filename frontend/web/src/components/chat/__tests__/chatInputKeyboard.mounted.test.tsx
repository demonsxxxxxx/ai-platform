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
  const { MemoryRouter } = await import("react-router-dom");
  const { AssistantUiTestProvider } = await import("../assistant-ui/__tests__/assistantUiTestProvider");
  const { AuthProvider } = await import("../../../hooks/useAuth");
  const { authApi } = await import("../../../services/api/auth");
  const originalBootstrap = authApi.bootstrapAuthContext;
  const originalUser = authApi.getCurrentUser;
  authApi.bootstrapAuthContext = async (request) => ({ status: "ready", protocol_version: 2, generation: request.generation });
  authApi.getCurrentUser = async () => ({
    id: "keyboard-user", tenant_id: "keyboard-tenant", username: "keyboard-user",
    email: "keyboard@example.test", roles: [], permissions: ["file:upload:document"], is_active: true,
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
    await act(async () => { root.render(<MemoryRouter><AuthProvider><AssistantUiTestProvider isRunning={props.isLoading}><ThreadPrimitive.Suggestion prompt="建议问题" send={false} data-test-suggestion>建议问题</ThreadPrimitive.Suggestion><ChatInput {...props} /></AssistantUiTestProvider></AuthProvider></MemoryRouter>); });
  };
  const textarea = () => container.querySelector("textarea")!;
  const type = async (value: string, cursor = value.length) => {
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(textarea(), value);
      textarea().setSelectionRange(cursor, cursor);
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
    await t.test("settling keeps the draft and blocks keyboard/button sends until continuation is ready", async () => {
      const { canContinueConversation, parsePublicContinuation } = await import("../../../services/api/session");
      const { publicContinuationMessage } = await import("../../../hooks/useAgent/publicTerminalPresentation");
      await type("停止后的新消息草稿");
      const continuation = parsePublicContinuation({ state: "settling", action: "wait", reason_code: "settling", workspace_saved: false });
      await render({ canSend: canContinueConversation(continuation), placeholder: publicContinuationMessage(continuation), sendBlockedReason: "continuation" });
      assert.equal(textarea().disabled, true);
      const sendButton = container.querySelector<HTMLButtonElement>('button[type="submit"]');
      assert.ok(sendButton === null || sendButton.disabled);
      await act(async () => sendButton?.click());
      assert.match(textarea().placeholder, /正在收尾/);
      // A temporary continuation wait never reuses the permission lock.
      const { default: lockI18n } = await import("../../../i18n");
      assert.equal(container.querySelector(`button[title="${lockI18n.t("chat.noPermission")}"]`), null);
      await key("Enter");
      assert.equal(sends.length, 0);
      assert.equal(textarea().value, "停止后的新消息草稿");
      await render({ canSend: false, placeholder: publicContinuationMessage(undefined, true), sendBlockedReason: "continuation" });
      assert.equal(textarea().disabled, true);
      assert.match(textarea().placeholder, /恢复状态/);
      await key("Enter");
      assert.equal(sends.length, 0);
      assert.equal(textarea().value, "停止后的新消息草稿");
      const ready = parsePublicContinuation({ state: "resumable", action: "continue", reason_code: "saved", workspace_saved: true });
      await render({ canSend: canContinueConversation(ready), placeholder: undefined, sendBlockedReason: undefined });
      assert.equal(textarea().disabled, false);
      assert.equal(container.querySelector<HTMLButtonElement>('[data-slot="composer-send"]')!.disabled, false);
      assert.equal(textarea().value, "停止后的新消息草稿");
      await type("");
    });
    await t.test("stopping keeps an editable draft and attachment while native and keyboard sends stay blocked", async () => {
      const attachment = { id: "stop-draft-file", key: "stop-draft-file", name: "next.txt", type: "document" as const,
        url: "/api/files/next", mimeType: "text/plain", size: 4 };
      await render({ canSend: false, canEdit: true, isLoading: true, isStopping: true,
        canStop: false, attachments: [attachment], sendBlockedReason: "synchronizing" });
      await type("下一轮草稿");
      assert.equal(textarea().disabled, false);
      assert.ok(container.querySelector("[data-composer-file-reference]"));
      await key("Enter");
      await act(async () => textarea().closest("form")!.requestSubmit());
      assert.equal(sends.length, 0);
      assert.equal(dialogOpen(), false, "Enter cannot request another stop");
      assert.equal(textarea().value, "下一轮草稿");
      await render({ canSend: true, canEdit: undefined, isLoading: false, isStopping: false, attachments: [], sendBlockedReason: undefined });
      assert.equal(textarea().value, "下一轮草稿");
      await type("");
    });
    await t.test("ordinary placeholder does not replace the permission reason or administrator action", async () => {
      await render({ canSend: false, placeholder: "向专家描述要完成的任务", sendBlockedReason: "permission" });
      const { default: i18n } = await import("../../../i18n");
      const permissionButton = container.querySelector<HTMLButtonElement>(`button[title="${i18n.t("chat.noPermission")}"]`);
      assert.ok(permissionButton);
      assert.equal(permissionButton.disabled, false);
      assert.equal(textarea().disabled, true);
      await render({ canSend: true, placeholder: undefined, sendBlockedReason: undefined });
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
      const scheduleFrame = globalThis.requestAnimationFrame;
      const pendingFrames: FrameRequestCallback[] = [];
      globalThis.requestAnimationFrame = (callback) => { pendingFrames.push(callback); return pendingFrames.length; };
      try {
        assert.equal((await key("Tab")).defaultPrevented, true);
        assert.equal(selectedCommand(), undefined);
        assert.equal(sends.length, 1);
        await type("/s");
        await act(async () => {
          for (const callback of pendingFrames.splice(0)) callback(0);
          document.dispatchEvent(new dom.window.Event("selectionchange", { bubbles: true }));
        });
        assert.equal(textarea().selectionStart, 2, "a previous command cannot move the new draft's caret");
        assert.equal((await key("Enter")).defaultPrevented, true);
        assert.equal(selectedCommand(), undefined);
        assert.equal(sends.length, 1);
      } finally { globalThis.requestAnimationFrame = scheduleFrame; }
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
      assert.equal(refreshCalls, 1, "typing only shows shortcut suggestions");
      await key("Enter");
      assert.equal(refreshCalls, 2, "explicit shortcut selection opens the same refresh seam");
      assert.equal(document.querySelector('[data-composer-model-panel] input')?.getAttribute("value"), "");
      await act(async () => document.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true })));

      await type("/");
      for (let i = 0; i < 8 && container.querySelector('[role="option"][aria-selected="true"]')?.getAttribute("data-composer-command-item") !== "model"; i += 1) {
        await key("ArrowDown");
      }
      assert.equal(container.querySelector('[role="option"][aria-selected="true"]')?.getAttribute("data-composer-command-item"), "model");
      await key("Enter");
      assert.equal(refreshCalls, 3, "slash menu selection refreshes as well");
      await act(async () => document.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
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
    await t.test("paths, unknown slash words and dollar text submit unchanged without invisible menu interception", async () => {
      const submitted: string[] = [];
      await render({ disableSlashCommands: false, isLoading: false, canSend: true,
        onSend: async (value) => { submitted.push(value); return { status: "accepted" }; } });
      for (const value of [
        "/workspace/.home/Desktop/project-board_2026.xlsx在哪？我指的是我的桌面",
        "/api/v1", "/unknown", "$100", "$变量", "/model is failing in production",
      ]) {
        await type(value);
        assert.equal(container.querySelector("[data-composer-command-menu]"), null);
        assert.equal((await key("Tab")).defaultPrevented, false);
        await key("Enter");
        assert.equal(submitted.at(-1), value);
        assert.equal(textarea().value, "");
      }
    });
    await t.test("Send submits literal slash text even while command suggestions are visible", async () => {
      const submitted: string[] = [];
      await render({ onSend: async (value) => { submitted.push(value); return { status: "accepted" }; } });
      for (const value of ["/", "/m", "/skill"]) {
        await type(value);
        assert.ok(container.querySelector("[data-composer-command-menu]"));
        await act(async () => container.querySelector<HTMLButtonElement>("button[aria-label='发送消息']")!.click());
        assert.equal(submitted.at(-1), value);
        assert.equal(textarea().value, "");
      }
    });
    await t.test("newline modifiers and Escape preserve slash drafts and do not choose a command", async () => {
      await type("/m");
      assert.equal((await key("Enter", { shiftKey: true })).defaultPrevented, false);
      assert.equal((await key("Tab", { shiftKey: true })).defaultPrevented, false);
      assert.equal(textarea().value, "/m");
      assert.ok(container.querySelector("[data-composer-command-menu]"));
      localStorage.setItem("newlineModifier", "ctrl");
      assert.equal((await key("Enter", { ctrlKey: true })).defaultPrevented, false);
      localStorage.removeItem("newlineModifier");
      assert.equal((await key("Escape")).defaultPrevented, true);
      assert.equal(textarea().value, "/m");
      assert.equal(container.querySelector("[data-composer-command-menu]"), null);
      await key("Enter");
      assert.equal(textarea().value, "", "Escape does not allow form submission to reopen commands");
    });
    await t.test("choosing an unavailable command retains the full draft and attachment", async () => {
      const attachment = { id: "slash-file", key: "slash-file", name: "draft.txt", type: "document" as const, url: "/api/files/draft", mimeType: "text/plain", size: 5 };
      await render({ attachments: [attachment], enableSkills: false });
      await type("保留正文 /skill");
      assert.ok(container.querySelector("[data-composer-command-item='skill']"));
      await act(async () => {
        const item = container.querySelector<HTMLButtonElement>("[data-composer-command-item='skill']")!;
        item.focus();
        item.click();
      });
      await act(async () => { await new Promise((resolve) => setTimeout(resolve, 30)); });
      assert.equal(document.activeElement === textarea(), true, "mouse selection returns editing focus after the menu unmounts");
      assert.equal(textarea().selectionStart, "保留正文 /skill".length);
      assert.equal(textarea().value, "保留正文 /skill");
      await type("保留正文 /skill 继续说明");
      assert.equal(textarea().value, "保留正文 /skill 继续说明");
      assert.ok(container.querySelector("[data-composer-file-reference]"));
      assert.ok(container.querySelector("[data-composer-chip-state='unavailable']"));
      await render({ attachments: [] });
    });
    await t.test("available shortcut selection consumes only its token and model selection never clears ordinary text", async () => {
      const selected: string[] = [];
      const models = [{ id: "model-a", value: "model-a", label: "模型甲" }, { id: "model-b", value: "model-b", label: "模型乙" }];
      const attachment = { id: "model-file", key: "model-file", name: "draft.txt", type: "document" as const, url: "/api/files/draft", mimeType: "text/plain", size: 5 };
      await render({ attachments: [attachment], availableModels: models, currentModelId: "model-a", onSelectModel: (id) => selected.push(id) });
      await type("请用 /mo 后续正文", 6);
      assert.ok(container.querySelector("[data-composer-command-item='model']"));
      await key("Tab");
      assert.equal(textarea().value, "请用  后续正文");
      assert.ok(document.querySelector("[data-composer-model-panel]"));
      await act(async () => document.querySelector<HTMLButtonElement>("[data-composer-model-option='model-b']")!.click());
      assert.deepEqual(selected, ["model-b"]);
      assert.equal(textarea().value, "请用  后续正文");
      assert.ok(container.querySelector("[data-composer-file-reference]"));
      await type("/model is failing in production");
      await act(async () => container.querySelector<HTMLButtonElement>("[data-composer-model-trigger]")!.click());
      await act(async () => document.querySelector<HTMLButtonElement>("[data-composer-model-option='model-a']")!.click());
      assert.equal(textarea().value, "/model is failing in production");
      await type("保留正文 /model");
      await key("Enter");
      assert.equal(textarea().value, "保留正文 ");
      await act(async () => document.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
      assert.equal(textarea().value, "保留正文 ");
      await render({ availableModels: [], onSelectModel: undefined, attachments: [] });
    });
    await t.test("skill selection and file picker keep the task body and authorized attachment", async () => {
      const skill = { name: "review", expected_version: "1", input_modes: [], requires_file: false, description: "Review", tags: [], enabled: true, source: "manual" as const, files: {}, file_count: 1, installed_from: "manual" as const, is_published: true, marketplace_is_active: true };
      const picked: string[] = [];
      const attachment = { id: "selection-file", key: "selection-file", name: "draft.txt", type: "document" as const, url: "/api/files/draft", mimeType: "text/plain", size: 5 };
      await render({ enableSkills: true, skills: [skill], onSelectSkill: (value) => picked.push(value.name), attachments: [attachment] });
      await type("正文 /skill");
      await key("Enter");
      assert.equal(textarea().value, "正文 ");
      await act(async () => document.querySelector<HTMLButtonElement>("[data-composer-skill-row='review']")!.click());
      assert.deepEqual(picked, ["review"]);
      assert.equal(textarea().value, "正文 ");
      assert.ok(container.querySelector("[data-composer-file-reference]"));
      await render({ acceptedFileTypes: undefined });
      let opened = 0;
      const file = container.querySelector<HTMLInputElement>("input[type='file']")!;
      const click = file.click;
      file.click = () => { opened += 1; };
      try {
        await type("正文 /file");
        await key("Tab");
        assert.equal(opened, 1);
        assert.equal(textarea().value, "正文 ");
        assert.ok(container.querySelector("[data-composer-file-reference]"));
      } finally { file.click = click; }
      await render({ acceptedFileTypes: [], enableSkills: false, skills: [], onSelectSkill: undefined, attachments: [] });
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
