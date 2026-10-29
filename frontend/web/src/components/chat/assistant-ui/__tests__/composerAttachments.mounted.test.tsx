import assert from "node:assert/strict";
import test from "node:test";
import { act, useState } from "react";
import { useAui } from "@assistant-ui/react";
import { installSelectTestDom } from "../../../common/__tests__/selectTestDom";
import { installBrowserAuthTestDb } from "../../../../hooks/__tests__/browserAuthTestDb";
import { Permission, type MessageAttachment, type UploadResult } from "../../../../types";
import type { ChatInputProps } from "../../chatInputTypes";

// Real Composer + upload controller, with only the external upload transport mocked.
test("composer uploads once, preserves authorized files after a rejected send, and fences late cancellation", async () => {
  const dom = installSelectTestDom();
  installBrowserAuthTestDb();
  Object.assign(globalThis, { localStorage: window.localStorage, sessionStorage: window.sessionStorage });
  Object.defineProperty(navigator, "locks", { configurable: true, value: { request: async (_key: string, _options: unknown, callback: () => Promise<unknown>) => callback() } });
  const { createLobeTestRoot } = await import("../../../common/__tests__/lobeUiTestRoot");
  const { AssistantUiTestProvider } = await import("./assistantUiTestProvider");
  const { ChatInput } = await import("../../ChatInput");
  const { useFileUpload } = await import("../../../../hooks/useFileUpload");
  const { AuthProvider, useAuth } = await import("../../../../hooks/useAuth");
  const { authApi } = await import("../../../../services/api/auth");
  const { uploadApi } = await import("../../../../services/api/upload");
  const original = { bootstrap: authApi.bootstrapAuthContext, user: authApi.getCurrentUser, config: uploadApi.getConfig, upload: uploadApi.uploadFile, remove: uploadApi.deleteFile };
  authApi.bootstrapAuthContext = async (request) => ({ status: "ready", protocol_version: 2, generation: request.generation });
  let principalId = "upload-user";
  authApi.getCurrentUser = async () => ({ id: principalId, tenant_id: "upload-tenant", username: principalId, email: "upload@example.test", roles: ["admin"], permissions: [Permission.FILE_UPLOAD_DOCUMENT], is_active: true, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" });
  uploadApi.getConfig = async () => { throw new Error("synthetic optional limits unavailable"); };
  let finishUpload!: (value: UploadResult) => void;
  let uploads = 0;
  let aborts = 0;
  uploadApi.uploadFile = () => ({ promise: new Promise((resolve) => { uploads++; finishUpload = resolve; }), abort: () => { aborts++; } });
  const removedKeys: string[] = [];
  uploadApi.deleteFile = async (key) => { removedKeys.push(key); return { file_id: key, lifecycle_state: "deleted", deletion_state: "deleted", reconcile_required: false }; };
  let accept = false;
  let deferredSend: Promise<Awaited<ReturnType<ChatInputProps["onSend"]>>> | undefined;
  let refreshUser!: ReturnType<typeof useAuth>["refreshUser"];
  let changeSession!: (scope: string | null, handoff?: string | null) => void;
  let runtime: ReturnType<typeof useAui>["composer"] | undefined;
  const sent: Parameters<ChatInputProps["onSend"]>[] = [];
  function Harness() {
    runtime = useAui().composer;
    refreshUser = useAuth().refreshUser;
    const [{ scope, handoff }, setScope] = useState<{ scope: string | null; handoff: string | null }>({ scope: "upload-session", handoff: null });
    changeSession = (scope, handoff = null) => setScope({ scope, handoff });
    const [attachments, setAttachments] = useState<MessageAttachment[]>([]);
    const controls = useFileUpload({ attachments, onAttachmentsChange: setAttachments });
    return <ChatInput attachments={attachments} onAttachmentsChange={setAttachments} uploadControls={controls}
      draftScopeKey={scope} draftScopeHandoffKey={handoff} attachmentScopeKey="upload-owner" disableSlashCommands enableSkills={false}
      onSend={async (...args) => { sent.push(args); return deferredSend ?? (accept ? { status: "accepted" } : { status: "recoverable_error", code: "file_required_for_skill" }); }}
      onStop={async () => "unavailable"} isLoading={false} agentOptionValues={{ model: "selected-model" }} />;
  }
  const container = document.createElement("div");
  const root = await createLobeTestRoot(container);
  const result = (): UploadResult => ({ key: "private-storage-ref", url: "/api/ai/files/authorized/download", name: "report.txt", type: "document", mimeType: "text/plain", size: 7 });
  const add = async (name: string) => {
    const picker = container.querySelector<HTMLInputElement>('input[type="file"]')!;
    Object.defineProperty(picker, "files", { configurable: true, value: [new File(["fixture"], name, { type: "text/plain" })] });
    await act(async () => picker.dispatchEvent(new Event("change", { bubbles: true })));
  };
  const send = () => container.querySelector<HTMLButtonElement>('button[aria-label="发送消息"]')!;
  try {
    await act(async () => root.render(<AuthProvider><AssistantUiTestProvider><Harness /></AssistantUiTestProvider></AuthProvider>));
    const input = container.querySelector("textarea")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(input, "处理附件");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await add("report.txt");
    assert.equal(uploads, 1);
    assert.equal(send().disabled, true);
    await act(async () => finishUpload(result()));
    assert.equal(send().disabled, false);
    await act(async () => send().click());
    assert.equal(sent.length, 1);
    assert.equal(sent[0][2]?.[0].key, "private-storage-ref");
    assert.equal(input.value, "处理附件");
    assert.ok(container.querySelector("[data-composer-file-reference]"));
    assert.doesNotMatch(JSON.stringify(runtime!.getState().attachments), /private-storage-ref|api\/ai\/files/);
    accept = true;
    await act(async () => send().click());
    assert.equal(sent.length, 2);
    assert.equal(uploads, 1, "retry must not upload again");
    assert.equal(container.querySelector("[data-composer-file-reference]"), null);
    await add("cancel.txt");
    const cancel = container.querySelector<HTMLButtonElement>('button[aria-label="取消上传"]');
    assert.ok(cancel);
    await act(async () => cancel.click());
    await act(async () => finishUpload({ ...result(), name: "cancel.txt" }));
    assert.equal(aborts, 1);
    assert.equal(container.querySelector("[data-composer-file-reference]"), null);
    assert.equal(runtime!.getState().attachments.length, 0);

    await add("old-account.txt");
    principalId = "replacement-user";
    await act(async () => { await refreshUser(); });
    await act(async () => finishUpload({ ...result(), key: "old-account-storage-ref", name: "old-account.txt" }));
    assert.equal(aborts, 2, "account replacement cancels the original native upload");
    assert.equal(runtime!.getState().attachments.length, 0);
    assert.equal(container.querySelector("[data-composer-file-reference]"), null);
    assert.ok(removedKeys.includes("old-account-storage-ref"), "late upload success is cleaned up after account replacement");

    await act(async () => changeSession(null));
    await add("first-session.txt");
    await act(async () => finishUpload({ ...result(), key: "first-session-ref", name: "first-session.txt" }));
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(input, "创建首个会话");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    let acceptFirstSend!: (outcome: Awaited<ReturnType<ChatInputProps["onSend"]>>) => void;
    deferredSend = new Promise((resolve) => { acceptFirstSend = resolve; });
    await act(async () => send().click());
    assert.equal(sent.length, 3);
    await act(async () => changeSession("created-session", "created-session"));
    await act(async () => acceptFirstSend({ status: "accepted" }));
    assert.equal(sent[2][0], "创建首个会话");
    assert.equal(sent[2][2]?.[0].key, "first-session-ref");
    assert.equal(input.value, "");
    assert.equal(runtime!.getState().attachments.length, 0);
    assert.equal(container.querySelector("[data-composer-file-reference]"), null);
    assert.equal(uploads, 4, "first-session handoff preserves one upload per selected file");
  } finally {
    await act(async () => root.unmount());
    authApi.bootstrapAuthContext = original.bootstrap;
    authApi.getCurrentUser = original.user;
    uploadApi.getConfig = original.config;
    uploadApi.uploadFile = original.upload;
    uploadApi.deleteFile = original.remove;
    dom.close();
  }
});

// Red DOM-count repro: three completed uploads must render one removable
// presentation each, not a file card plus a mirrored removable chip.
test("three composer attachments render one removable presentation each", async () => {
  const dom = installSelectTestDom();
  installBrowserAuthTestDb();
  Object.assign(globalThis, { localStorage: window.localStorage, sessionStorage: window.sessionStorage });
  Object.defineProperty(navigator, "locks", { configurable: true, value: { request: async (_key: string, _options: unknown, callback: () => Promise<unknown>) => callback() } });
  const { createLobeTestRoot } = await import("../../../common/__tests__/lobeUiTestRoot");
  const { AssistantUiTestProvider } = await import("./assistantUiTestProvider");
  const { ChatInput } = await import("../../ChatInput");
  const { useFileUpload } = await import("../../../../hooks/useFileUpload");
  const { AuthProvider } = await import("../../../../hooks/useAuth");
  const { authApi } = await import("../../../../services/api/auth");
  const { uploadApi } = await import("../../../../services/api/upload");
  const original = { bootstrap: authApi.bootstrapAuthContext, user: authApi.getCurrentUser, config: uploadApi.getConfig, upload: uploadApi.uploadFile, remove: uploadApi.deleteFile };
  authApi.bootstrapAuthContext = async (request) => ({ status: "ready", protocol_version: 2, generation: request.generation });
  authApi.getCurrentUser = async () => ({ id: "file-count-user", tenant_id: "file-count-tenant", username: "file-count-user", email: "file-count@example.test", roles: ["admin"], permissions: [Permission.FILE_UPLOAD_DOCUMENT], is_active: true, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" });
  uploadApi.getConfig = async () => { throw new Error("synthetic optional limits unavailable"); };
  const pending: Array<(value: UploadResult) => void> = [];
  uploadApi.uploadFile = () => ({ promise: new Promise<UploadResult>((resolve) => { pending.push(resolve); }), abort: () => {} });
  uploadApi.deleteFile = async (key) => ({ file_id: key, lifecycle_state: "deleted", deletion_state: "deleted", reconcile_required: false });
  function Harness() {
    const [attachments, setAttachments] = useState<MessageAttachment[]>([]);
    const controls = useFileUpload({ attachments, onAttachmentsChange: setAttachments });
    return <ChatInput attachments={attachments} onAttachmentsChange={setAttachments} uploadControls={controls}
      attachmentScopeKey="file-count-owner" disableSlashCommands enableSkills={false}
      onSend={async () => ({ status: "accepted" })} onStop={async () => "unavailable"} isLoading={false} />;
  }
  const container = document.createElement("div");
  const root = await createLobeTestRoot(container);
  const add = async (name: string) => {
    const picker = container.querySelector<HTMLInputElement>('input[type="file"]')!;
    Object.defineProperty(picker, "files", { configurable: true, value: [new File(["fixture"], name, { type: "application/pdf" })] });
    await act(async () => picker.dispatchEvent(new Event("change", { bubbles: true })));
  };
  const complete = () => ({ key: "private-storage-ref", url: "/api/ai/files/authorized/preview", name: "fixture.pdf", type: "document" as const, mimeType: "application/pdf", size: 7 });
  try {
    await act(async () => root.render(<AuthProvider><AssistantUiTestProvider><Harness /></AssistantUiTestProvider></AuthProvider>));
    for (const name of ["1.3.1.pdf", "2.3.2.pdf", "3.2.P.2.2.pdf"]) {
      await add(name);
      await act(async () => pending.shift()!({ ...complete(), name }));
    }
    const cards = container.querySelectorAll("[data-composer-file-reference]").length;
    const fileChips = container.querySelectorAll('[data-composer-chip-kind="file"]').length;
    assert.equal(cards, 3, "each upload keeps its file card");
    assert.equal(fileChips, 0, "an upload must not also render as a mirrored composer chip");
    assert.equal(container.querySelectorAll("button[aria-label^='移除附件']").length, 3);
  } finally {
    await act(async () => root.unmount());
    authApi.bootstrapAuthContext = original.bootstrap;
    authApi.getCurrentUser = original.user;
    uploadApi.getConfig = original.config;
    uploadApi.uploadFile = original.upload;
    uploadApi.deleteFile = original.remove;
    dom.close();
  }
});
