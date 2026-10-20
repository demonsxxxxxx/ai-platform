import assert from "node:assert/strict";
import test, { after } from "node:test";
import { installSelectTestDom } from "./selectTestDom";
import { createLobeTestRoot } from "./lobeUiTestRoot";

const dom = installSelectTestDom();
Object.assign(globalThis, { sessionStorage: window.sessionStorage });
after(() => dom.close());
const { act, createElement } = await import("react");
const { MemoryRouter } = await import("react-router-dom");
const { SelectionActionPopover } = await import("../SelectionActionPopover");
const { SELECTION_ACTION_EVENT, consumePendingSelectionActionPrompt } = await import("../selectionActionPrompt");

async function mount(path = "/chat") {
  const container = document.createElement("div");
  const root = await createLobeTestRoot(container);
  await act(async () => root.render(createElement(MemoryRouter, { initialEntries: [path] },
    createElement("p", { "data-selection-fixture": true }, "合成选择内容"),
    createElement(SelectionActionPopover))));
  const selectText = async () => {
    const range = document.createRange();
    range.selectNodeContents(container.querySelector("[data-selection-fixture]")!);
    // The selection is real; only layout geometry is synthetic in JSDOM.
    range.getBoundingClientRect = () => new window.DOMRect(100, 100, 160, 24);
    window.getSelection()!.removeAllRanges();
    window.getSelection()!.addRange(range);
    await act(async () => {
      document.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, 60));
    });
  };
  return { container, selectText, async close() { await act(async () => root.unmount()); window.getSelection()?.removeAllRanges(); } };
}

test("selection actions use the themed library portal and dispatch the unchanged prompt", async () => {
  const view = await mount();
  let prompt = "";
  const listener = (event: Event) => { prompt = (event as CustomEvent<{ prompt: string }>).detail.prompt; };
  window.addEventListener(SELECTION_ACTION_EVENT, listener);
  try {
    await view.selectText();
    const popup = document.querySelector<HTMLElement>("[data-selection-action-popover]")!;
    assert.ok(popup);
    assert.ok(popup.closest("[data-lobe-portal-host]"));
    const ask = popup.querySelector<HTMLButtonElement>("button")!;
    await act(async () => { ask.click(); });
    assert.match(prompt, /合成选择内容/);
    assert.equal(window.getSelection()!.isCollapsed, true);
    assert.equal(document.querySelector("[data-selection-action-popover]"), null);
  } finally { window.removeEventListener(SELECTION_ACTION_EVENT, listener); await view.close(); }
});

test("selection action outside chat preserves pending navigation prompt", async () => {
  const view = await mount("/agents");
  try {
    await view.selectText();
    await act(async () => { document.querySelector<HTMLButtonElement>("[data-selection-action-popover] button:last-child")!.click(); });
    assert.match(consumePendingSelectionActionPrompt()!, /合成选择内容/);
    assert.equal(consumePendingSelectionActionPrompt(), null);
  } finally { await view.close(); }
});

test("Escape dismisses the selection popover without reopening on keyup", async () => {
  const view = await mount();
  try {
    await view.selectText();
    assert.ok(document.querySelector("[data-selection-action-popover]"));
    await act(async () => {
      document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
      document.body.dispatchEvent(new KeyboardEvent("keyup", { key: "Escape", bubbles: true, cancelable: true }));
      await new Promise((resolve) => setTimeout(resolve, 60));
    });
    assert.equal(document.querySelector("[data-selection-action-popover]"), null);
  } finally { await view.close(); }
});
