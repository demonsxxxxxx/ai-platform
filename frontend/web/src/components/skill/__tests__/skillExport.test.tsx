import assert from "node:assert/strict";
import test from "node:test";
import JSZip from "jszip";
// @ts-expect-error jsdom runtime import.
import { JSDOM } from "jsdom";
import { skillExportContent } from "../skillExport";
import { exportProjectZip } from "../../../utils/exportProjectZip";
import { skillApi } from "../../../services/api/skill";

const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost/skills" });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, localStorage: dom.window.localStorage, sessionStorage: dom.window.sessionStorage, HTMLElement: dom.window.HTMLElement, CustomEvent: dom.window.CustomEvent, IS_REACT_ACT_ENVIRONMENT: true });
Object.defineProperty(globalThis, "navigator", { configurable: true, value: dom.window.navigator });
const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useSkills } = await import("../../../hooks/useSkills");

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => { resolve = yes; });
  return { resolve, promise };
}

test("file API binary content is strict base64 bytes; text and empty files remain unchanged", () => {
  assert.deepEqual(skillExportContent({ is_binary: true, content: "AP+A" }), Uint8Array.of(0, 255, 128));
  assert.equal(skillExportContent({ content: "中文\n" }), "中文\n");
  assert.equal(skillExportContent({ content: "" }), "");
  assert.deepEqual(skillExportContent({ is_binary: true, content: "" }), new Uint8Array());
  for (const content of ["bad!", "A", "AB==", "AP+A\n"]) {
    assert.throws(() => skillExportContent({ is_binary: true, content }), /skill_file_content_invalid/);
  }
});

test("ZIP round trip preserves binary, UTF-8 and empty bytes without fetching URLs", async () => {
  const original = { create: URL.createObjectURL, revoke: URL.revokeObjectURL, click: dom.window.HTMLAnchorElement.prototype.click, fetch: globalThis.fetch };
  let captured!: Blob; let clicks = 0; let revoked = 0; let fetches = 0;
  URL.createObjectURL = (blob) => { captured = blob as Blob; return "blob:synthetic"; };
  URL.revokeObjectURL = () => { revoked += 1; };
  dom.window.HTMLAnchorElement.prototype.click = () => { clicks += 1; };
  globalThis.fetch = async () => { fetches += 1; throw new Error("unexpected external fetch"); };
  try {
    const bytes = Uint8Array.of(137, 80, 78, 71, 13, 10, 26, 10, 255, 0);
    await exportProjectZip({ "SKILL.md": "中文\n", "图片.png": bytes, "empty.txt": "" }, "synthetic");
    const zip = await JSZip.loadAsync(await captured.arrayBuffer());
    assert.deepEqual(await zip.file("图片.png")!.async("uint8array"), bytes);
    assert.equal(await zip.file("SKILL.md")!.async("string"), "中文\n");
    assert.equal((await zip.file("empty.txt")!.async("uint8array")).length, 0);
    assert.equal(clicks, 1); assert.equal(revoked, 1);
    let checks = 0;
    await assert.rejects(exportProjectZip({ "SKILL.md": "body" }, "stale", () => ++checks === 1), { name: "AbortError" });
    assert.equal(clicks, 1);
    dom.window.HTMLAnchorElement.prototype.click = () => { throw new Error("blocked click"); };
    await assert.rejects(exportProjectZip({ "SKILL.md": "body" }, "failure"), /blocked click/);
    assert.equal(revoked, 2);
    assert.equal(document.querySelectorAll("a").length, 0);
    assert.equal(fetches, 0);
  } finally {
    URL.createObjectURL = original.create; URL.revokeObjectURL = original.revoke;
    dom.window.HTMLAnchorElement.prototype.click = original.click; globalThis.fetch = original.fetch;
  }
});

test("complete export fails on any file error and rejects a stale owner after detail", async () => {
  const original = { list: skillApi.list, get: skillApi.get, file: skillApi.getFile };
  skillApi.list = async () => ({ skills: [], total: 0, skip: 0, limit: 10, available_tags: [], effective_permissions: [], effective_permissions_known: true, catalog_read_resolved: true });
  skillApi.get = async () => ({ files: ["SKILL.md", "image.png", "empty.txt"] });
  skillApi.getFile = async (_name, path) => path === "image.png" ? { is_binary: true, content: "AP+A" } : { content: path === "empty.txt" ? "" : "body" };
  const container = document.createElement("div"); const root = createRoot(container);
  let snapshot!: ReturnType<typeof useSkills>;
  function Probe() { snapshot = useSkills(); return null; }
  try {
    await act(async () => { root.render(createElement(Probe)); });
    let files: Record<string, string | Uint8Array> | null = null;
    await act(async () => { files = await snapshot.getSkillExportFiles("synthetic"); });
    assert.deepEqual(files, { "SKILL.md": "body", "image.png": Uint8Array.of(0, 255, 128), "empty.txt": "" });
    skillApi.getFile = async () => { throw new Error("synthetic 403"); };
    await act(async () => { files = await snapshot.getSkillExportFiles("synthetic"); });
    assert.equal(files, null);
    const held = deferred<{ files: string[] }>(); let current = true; let fileCalls = 0;
    skillApi.get = () => held.promise; skillApi.getFile = async () => { fileCalls += 1; return { content: "new identity" }; };
    let pending!: ReturnType<typeof snapshot.getSkillExportFiles>;
    await act(async () => { pending = snapshot.getSkillExportFiles("synthetic", () => current); });
    current = false;
    await act(async () => { held.resolve({ files: ["SKILL.md"] }); assert.equal(await pending, null); });
    assert.equal(fileCalls, 0);
  } finally {
    await act(async () => root.unmount());
    skillApi.list = original.list; skillApi.get = original.get; skillApi.getFile = original.file;
  }
});
