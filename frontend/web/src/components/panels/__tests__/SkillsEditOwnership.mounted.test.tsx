import assert from "node:assert/strict";
import test from "node:test";
// @ts-expect-error jsdom runtime import.
import { JSDOM } from "jsdom";
import type { SkillResponse, UserSkillDetail } from "../../../types/skill";
import { skillApi } from "../../../services/api/skill";

const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost/skills" });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, localStorage: dom.window.localStorage, sessionStorage: dom.window.sessionStorage, HTMLElement: dom.window.HTMLElement, CustomEvent: dom.window.CustomEvent, IS_REACT_ACT_ENVIRONMENT: true });
Object.defineProperty(globalThis, "navigator", { configurable: true, value: dom.window.navigator });
const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const { MemoryRouter } = await import("react-router-dom");
const { AuthProvider } = await import("../../../hooks/useAuth");
const { useSkillsActions } = await import("../SkillsPanel/useSkillsActions");
await import("../../../i18n/index");

function skill(name: string): SkillResponse {
  return { name, description: name, tags: [], enabled: true, source: "manual", files: {}, file_count: 3, installed_from: "manual", is_published: false, marketplace_is_active: true };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => { resolve = yes; });
  return { promise, resolve };
}
async function harness() {
  const originalList = skillApi.list;
  skillApi.list = async () => ({ skills: [], total: 0, skip: 0, limit: 10, available_tags: [], effective_permissions: [], effective_permissions_known: true, catalog_read_resolved: true });
  let snapshot!: ReturnType<typeof useSkillsActions>;
  function Probe() { snapshot = useSkillsActions(); return null; }
  const container = document.createElement("div"); document.body.appendChild(container); const root = createRoot(container);
  await act(async () => { root.render(createElement(AuthProvider, null, createElement(MemoryRouter, null, createElement(Probe)))); });
  return { current: () => snapshot, async close() { await act(async () => root.unmount()); container.remove(); skillApi.list = originalList; } };
}

test("detail failure leaves the editor closed instead of substituting catalog metadata", async () => {
  const originalGet = skillApi.get;
  skillApi.get = async () => { throw new Error("synthetic detail failed"); };
  const view = await harness();
  try {
    await act(async () => view.current().handleEdit(skill("one")));
    assert.equal(view.current().showModal, false);
    assert.equal(view.current().editingSkill, null);
  } finally { await view.close(); skillApi.get = originalGet; }
});

test("late detail cannot reopen a cancelled editor or replace a newer selection", async () => {
  const originalGet = skillApi.get;
  const old = deferred<UserSkillDetail>();
  skillApi.get = async (name) => name === "one" ? old.promise : { skill_name: name, description: name, files: ["SKILL.md"] };
  const view = await harness();
  try {
    let first!: Promise<void>;
    await act(async () => { first = view.current().handleEdit(skill("one")); });
    await act(async () => view.current().handleEdit(skill("two")));
    await act(async () => { old.resolve({ skill_name: "one", files: ["SKILL.md"] }); await first; });
    assert.equal(view.current().editingSkill?.name, "two");
    assert.equal(view.current().showModal, true);
    const pending = deferred<UserSkillDetail>(); skillApi.get = () => pending.promise;
    let reopening!: Promise<void>;
    await act(async () => { reopening = view.current().handleEdit(skill("three")); });
    await act(async () => view.current().handleCancel());
    await act(async () => { pending.resolve({ skill_name: "three", files: ["SKILL.md"] }); await reopening; });
    assert.equal(view.current().showModal, false);
  } finally { await view.close(); skillApi.get = originalGet; }
});

test("save passes explicit removals and leaves unwritten lazy files retained", async () => {
  const originalGet = skillApi.get; const originalUpdate = skillApi.update;
  skillApi.get = async () => ({ skill_name: "one", files: ["SKILL.md", "script.py", "image.png", "remove.txt"] });
  const calls: Parameters<typeof skillApi.update>[] = [];
  skillApi.update = async (...args) => { calls.push(args); return { message: "Updated" }; };
  const view = await harness();
  try {
    await act(async () => view.current().handleEdit(skill("one")));
    await act(async () => { assert.equal(await view.current().handleSave({ name: "one", description: "new", tags: [], content: "body", files: { "SKILL.md": "body" }, deletedFiles: ["remove.txt"] }), true); });
    assert.deepEqual(calls[0][1].files, { "SKILL.md": "body" });
    assert.deepEqual(calls[0][1].deletedFiles, ["remove.txt"]);
  } finally { await view.close(); skillApi.get = originalGet; skillApi.update = originalUpdate; }
});

test("export never downloads a partial archive and ignores a session changed mid-read", async () => {
  const { toast } = await import("@lobehub/ui/base-ui");
  const { AUTH_SESSION_MARKER_KEY } = await import("../../../services/api/token");
  const old = { get: skillApi.get, file: skillApi.getFile, create: URL.createObjectURL, success: toast.success, error: toast.error };
  let downloads = 0; let successes = 0; let errors = 0;
  URL.createObjectURL = () => { downloads += 1; return "blob:synthetic"; };
  toast.success = () => { successes += 1; return { id: "success", close() {}, update() {} }; };
  toast.error = () => { errors += 1; return { id: "error", close() {}, update() {} }; };
  skillApi.get = async () => ({ files: ["SKILL.md", "missing.png"] });
  skillApi.getFile = async (_name, path) => {
    if (path === "missing.png") throw new Error("synthetic file request failed");
    return { content: "body" };
  };
  const view = await harness();
  try {
    await act(async () => view.current().handleExportZip("one"));
    assert.equal(downloads, 0); assert.equal(successes, 0); assert.equal(errors, 1);
    const held = deferred<UserSkillDetail>(); let reads = 0;
    skillApi.get = () => held.promise;
    skillApi.getFile = async () => { reads += 1; return { content: "new session content" }; };
    let pending!: Promise<void>;
    await act(async () => { pending = view.current().handleExportZip("one"); });
    localStorage.setItem(AUTH_SESSION_MARKER_KEY, "synthetic-next-generation");
    await act(async () => { held.resolve({ files: ["SKILL.md"] }); await pending; });
    assert.equal(reads, 0); assert.equal(downloads, 0); assert.equal(successes, 0); assert.equal(errors, 1);
  } finally {
    await view.close(); localStorage.removeItem(AUTH_SESSION_MARKER_KEY);
    skillApi.get = old.get; skillApi.getFile = old.file; URL.createObjectURL = old.create;
    toast.success = old.success; toast.error = old.error;
  }
});

test("an export resolving after unmount cannot trigger a download", async () => {
  const oldGet = skillApi.get; const oldCreate = URL.createObjectURL;
  const held = deferred<UserSkillDetail>(); let downloads = 0;
  skillApi.get = () => held.promise;
  URL.createObjectURL = () => { downloads += 1; return "blob:synthetic"; };
  const view = await harness();
  try {
    let pending!: Promise<void>;
    await act(async () => { pending = view.current().handleExportZip("one"); });
    await view.close();
    held.resolve({ files: [] }); await pending;
    assert.equal(downloads, 0);
  } finally { skillApi.get = oldGet; URL.createObjectURL = oldCreate; }
});
