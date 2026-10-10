import assert from "node:assert/strict";
import test from "node:test";
// @ts-expect-error jsdom runtime import.
import { JSDOM } from "jsdom";
import { skillApi } from "../../../services/api/skill";
import type { SkillFormSubmission } from "../SkillForm.types";
import type { SkillFileResponse, SkillResponse } from "../../../types/skill";

function skill(name: string): SkillResponse {
  return { name, description: name, tags: [], enabled: true, source: "manual", content: "", files: {}, filePaths: ["SKILL.md", "script.py", "image.png"], file_count: 3, installed_from: "manual", is_published: false, marketplace_is_active: true };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost/skills", pretendToBeVisual: true });
Object.assign(globalThis, { window: dom.window, Window: dom.window.Window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, HTMLInputElement: dom.window.HTMLInputElement, MutationObserver: dom.window.MutationObserver, Event: dom.window.Event, MouseEvent: dom.window.MouseEvent, getComputedStyle: dom.window.getComputedStyle, requestAnimationFrame: dom.window.requestAnimationFrame.bind(dom.window), cancelAnimationFrame: dom.window.cancelAnimationFrame.bind(dom.window), IS_REACT_ACT_ENVIRONMENT: true });
Object.defineProperty(globalThis, "navigator", { configurable: true, value: dom.window.navigator });
dom.window.Range.prototype.getClientRects = () => [] as unknown as DOMRectList;
dom.window.Range.prototype.getBoundingClientRect = () => new dom.window.DOMRect();
const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
await import("../../../i18n/index");
const { SkillForm } = await import("../SkillForm");

async function harness(initial: SkillResponse) {
  const container = document.createElement("div"); document.body.appendChild(container);
  const root = createRoot(container);
  const saves: SkillFormSubmission[] = [];
  const render = async (value: SkillResponse) => { await act(async () => { root.render(createElement(SkillForm, { skill: value, onCancel() {}, async onSave(data) { saves.push(data); return true; } })); }); };
  await render(initial);
  return { container, saves, render, async submit() { await act(async () => { container.querySelector("form")!.dispatchEvent(new dom.window.Event("submit", { bubbles: true, cancelable: true })); }); }, async close() { await act(async () => root.unmount()); container.remove(); } };
}

test("metadata-only save preserves unopened text and binary bytes", async () => {
  const original = skillApi.getFile;
  const reads: string[] = [];
  skillApi.getFile = async (_name, path) => { reads.push(path); return { content: "# Authoritative body" }; };
  const view = await harness(skill("one"));
  try {
    await view.submit();
    assert.deepEqual(reads, ["SKILL.md"]);
    assert.equal(view.saves.length, 1);
    assert.deepEqual(Object.keys(view.saves[0].files!), ["SKILL.md"]);
    assert.deepEqual(view.saves[0].deletedFiles, []);
    assert.match(view.saves[0].content, /Authoritative body/);
  } finally { await view.close(); skillApi.getFile = original; }
});

test("failed SKILL.md reads block save and never use a template", async () => {
  const original = skillApi.getFile;
  skillApi.getFile = async () => { throw new Error("synthetic unavailable"); };
  const view = await harness(skill("one"));
  try { await view.submit(); assert.equal(view.saves.length, 0); } finally { await view.close(); skillApi.getFile = original; }
});

test("late reads from the previous skill cannot overwrite current markdown", async () => {
  const original = skillApi.getFile;
  const old = deferred<SkillFileResponse>();
  skillApi.getFile = async (name) => name === "one" ? old.promise : { content: "# Current owner" };
  const view = await harness(skill("one"));
  try {
    await view.render(skill("two"));
    await act(async () => old.resolve({ content: "# Stale owner" }));
    await view.submit();
    assert.equal(view.saves.length, 1);
    assert.match(view.saves[0].content, /Current owner/);
    assert.doesNotMatch(view.saves[0].content, /Stale owner/);
  } finally { await view.close(); skillApi.getFile = original; }
});


test("removing an earlier tab does not retarget a pending file read", async () => {
  const original = skillApi.getFile;
  const script = deferred<SkillFileResponse>();
  skillApi.getFile = async (_name, path) => path === "script.py" ? script.promise : { content: "# Main" };
  const value = skill("one"); value.filePaths = ["SKILL.md", "a.txt", "script.py"];
  const view = await harness(value);
  try {
    await act(async () => { view.container.querySelector<HTMLButtonElement>('button[title="script.py"]')!.click(); });
    await act(async () => { view.container.querySelector<HTMLElement>('button[title="a.txt"] [role="button"]')!.click(); });
    await act(async () => script.resolve({ content: "print('loaded script')" }));
    assert.match(view.container.textContent || "", /loaded script/);
    const pathInput = view.container.querySelector<HTMLInputElement>(".skill-file-path input")!;
    assert.equal(pathInput.value, "script.py");
    assert.equal(pathInput.readOnly, false);
    await view.submit();
    assert.deepEqual(view.saves[0].deletedFiles, ["a.txt"]);
    assert.deepEqual(Object.keys(view.saves[0].files!), ["SKILL.md"]);
  } finally { await view.close(); skillApi.getFile = original; }
});

test("removing a pending file does not copy its text onto the next file", async () => {
  const original = skillApi.getFile;
  const removed = deferred<SkillFileResponse>();
  skillApi.getFile = async (_name, path) => path === "a.txt" ? removed.promise : { content: "# Main" };
  const value = skill("one"); value.filePaths = ["SKILL.md", "a.txt", "script.py"];
  const view = await harness(value);
  try {
    await act(async () => { view.container.querySelector<HTMLButtonElement>('button[title="a.txt"]')!.click(); });
    await act(async () => { view.container.querySelector<HTMLElement>('button[title="a.txt"] [role="button"]')!.click(); });
    await act(async () => removed.resolve({ content: "stale removed bytes" }));
    assert.doesNotMatch(view.container.textContent || "", /stale removed bytes/);
    await view.submit();
    assert.deepEqual(view.saves[0].deletedFiles, ["a.txt"]);
    assert.deepEqual(Object.keys(view.saves[0].files!), ["SKILL.md"]);
  } finally { await view.close(); skillApi.getFile = original; }
});
