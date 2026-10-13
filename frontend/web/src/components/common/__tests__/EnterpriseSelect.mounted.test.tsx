import assert from "node:assert/strict";
import test from "node:test";
import { installSelectTestDom } from "./selectTestDom.ts";

const dom = installSelectTestDom();
const React = await import("react");
const { createRoot } = await import("react-dom/client");
const { EnterpriseSelect } = await import("../EnterpriseSelect.tsx");

const options = [
  { value: "a", label: "Alpha" },
  { value: "blocked", label: "Blocked", disabled: true },
  { value: "b", label: "Beta" },
];

async function mount(disabled = false) {
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  const changes: string[] = [];
  let submissions = 0;
  function Probe() {
    const [value, setValue] = React.useState("a");
    return React.createElement("form", { onSubmit: (event: React.FormEvent) => {
      event.preventDefault(); submissions += 1;
    } }, React.createElement(EnterpriseSelect, {
      "aria-label": "Synthetic choice", name: "choice", value, options, disabled,
      onChange: (next) => { changes.push(next); setValue(next); },
    }));
  }
  await React.act(async () => root.render(React.createElement(Probe)));
  const trigger = container.querySelector<HTMLButtonElement>('[role="combobox"]')!;
  async function key(target: HTMLElement, value: string) {
    await React.act(async () => {
      target.dispatchEvent(new KeyboardEvent("keydown", { key: value, bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
  return { container, trigger, changes, key, submissions: () => submissions,
    async close() { await React.act(async () => root.unmount()); container.remove(); } };
}

test("keyboard selects an enabled option exactly once, closes and restores focus", async () => {
  const view = await mount();
  try {
    assert.equal(view.trigger.textContent?.trim(), "Alpha");
    await view.key(view.trigger, "ArrowDown");
    const list = document.querySelector<HTMLElement>('[role="listbox"]');
    assert.ok(list);
    assert.equal(view.trigger.getAttribute("aria-controls"), list.id);
    await view.key(document.activeElement as HTMLElement, "End");
    assert.equal(document.activeElement?.getAttribute("data-value"), "b");
    await view.key(document.activeElement as HTMLElement, "Enter");
    assert.deepEqual(view.changes, ["b"]);
    assert.equal(view.trigger.textContent?.trim(), "Beta");
    assert.equal(document.querySelector('[role="listbox"]'), null);
    assert.equal(document.activeElement, view.trigger);
    assert.equal(new dom.FormData(view.container.querySelector("form")!).get("choice"), "b");
    assert.equal(view.submissions(), 0);
  } finally { await view.close(); }
});

test("Escape dismisses without changing the value or submitting the parent form", async () => {
  const view = await mount();
  try {
    await view.key(view.trigger, "ArrowDown");
    await view.key(document.activeElement as HTMLElement, "Escape");
    assert.equal(document.querySelector('[role="listbox"]'), null);
    assert.deepEqual(view.changes, []);
    assert.equal(document.activeElement, view.trigger);
    assert.equal(view.submissions(), 0);
  } finally { await view.close(); }
});

test("Escape closes the select without closing its host editor", async () => {
  const view = await mount();
  let editorCloseRequests = 0;
  const editorKeyDown = (event: KeyboardEvent) => {
    if (event.key === "Escape") editorCloseRequests += 1;
  };
  document.addEventListener("keydown", editorKeyDown);
  try {
    await view.key(view.trigger, "ArrowDown");
    await view.key(document.activeElement as HTMLElement, "Escape");
    assert.equal(document.querySelector('[role="listbox"]'), null);
    assert.equal(editorCloseRequests, 0);
    assert.equal(document.activeElement, view.trigger);
  } finally {
    document.removeEventListener("keydown", editorKeyDown);
    await view.close();
  }
});

test("disabled choices and disabled triggers cannot change the selection", async () => {
  const view = await mount();
  try {
    await view.key(view.trigger, "ArrowDown");
    const blocked = document.querySelector<HTMLElement>('[data-value="blocked"]')!;
    assert.equal(blocked.getAttribute("aria-disabled"), "true");
    await React.act(async () => blocked.click());
    assert.deepEqual(view.changes, []);
    await view.key(document.activeElement as HTMLElement, "ArrowDown");
    assert.equal(document.activeElement?.getAttribute("data-value"), "b");
  } finally { await view.close(); }
  const disabledView = await mount(true);
  try {
    assert.equal(disabledView.trigger.disabled, true);
    await React.act(async () => disabledView.trigger.click());
    assert.equal(document.querySelector('[role="listbox"]'), null);
    assert.deepEqual(disabledView.changes, []);
  } finally { await disabledView.close(); }
});
