import assert from "node:assert/strict";
import test from "node:test";
import { act } from "react";
import { installSelectTestDom } from "../../common/__tests__/selectTestDom";
import { createLobeTestRoot } from "../../common/__tests__/lobeUiTestRoot";
import type { FileEntry } from "../SkillForm.types";

const fixture = installSelectTestDom();
test.after(() => fixture.close());
const { FileTabs } = await import("../FileTabs");

test("file tabs keep caller indices, keyboard focus and the existing editor draft", async () => {
  const files: FileEntry[] = [
    { path: "SKILL.md", content: "main" },
    { path: "notes/a.txt", content: "a" },
    { path: "notes/b.txt", content: "b" },
  ];
  const selected: number[] = [];
  const removed: number[] = [];
  const container = document.createElement("div");
  const root = await createLobeTestRoot(container);
  const render = (entries: FileEntry[], active: number) => root.render(<>
    <FileTabs files={entries} activeFileIndex={active} onSelect={(index) => selected.push(index)}
      onRemove={(index) => removed.push(index)} untitledLabel="未命名" panelId="file-editor" />
    <div id="file-editor" role="tabpanel" aria-labelledby={`file-editor-tab-${active}`}>
      <textarea defaultValue="initial draft" />
    </div>
  </>);
  const tabs = () => Array.from(container.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
  const key = async (name: string) => {
    await act(async () => document.activeElement!.dispatchEvent(
      new window.KeyboardEvent("keydown", { key: name, bubbles: true, cancelable: true }),
    ));
  };
  try {
    await act(async () => render(files, 0));
    const editor = container.querySelector("textarea")!;
    editor.value = "unsaved local draft";
    assert.equal(tabs().length, 3);
    assert.equal(tabs()[0].getAttribute("aria-controls"), "file-editor");
    await act(async () => tabs()[0].focus());
    await key("ArrowRight");
    assert.ok(document.activeElement === tabs()[1], "ArrowRight moves tab focus");
    await key("End");
    assert.ok(document.activeElement === tabs()[2], "End reaches the last tab");
    await key("Home");
    assert.ok(document.activeElement === tabs()[0], "Home reaches the first tab");
    assert.deepEqual(selected, [], "manual activation does not select while moving focus");
    await act(async () => tabs()[1].click());
    assert.deepEqual(selected, [1]);
    await act(async () => render(files, 1));
    const close = container.querySelector<HTMLButtonElement>('button[aria-label="关闭 a.txt"]');
    assert.ok(close);
    assert.equal(close.tabIndex, 0);
    assert.equal(close.closest('[role="tab"]'), null, "close is a sibling action, not a nested button");
    await act(async () => {
      close.click();
      render([files[0], files[2]], 1);
    });
    await act(async () => { await new Promise(requestAnimationFrame); });
    assert.deepEqual(removed, [1]);
    assert.equal(files.length, 3, "the caller owns the files collection");
    assert.ok(document.activeElement === tabs()[1], "closing returns focus to the caller's selected tab");
    assert.match(tabs()[1].textContent ?? "", /b.txt/);
    assert.equal(container.querySelector('[role="tabpanel"]')!.getAttribute("aria-labelledby"), tabs()[1].id);
    assert.ok(container.querySelector("textarea") === editor, "tab actions keep the existing editor node");
    assert.equal(editor.value, "unsaved local draft");
  } finally {
    await act(async () => root.unmount());
  }
});
