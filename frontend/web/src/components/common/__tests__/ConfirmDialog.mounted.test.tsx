import assert from "node:assert/strict";
import test from "node:test";
import { installSelectTestDom } from "./selectTestDom";
import { createLobeTestRoot } from "./lobeUiTestRoot";

const dom = installSelectTestDom();
test.after(() => dom.close());
const { act, createElement, useState } = await import("react");
const { ConfirmDialog } = await import("../ConfirmDialog");

test("confirmation locks dismissal while busy and restores the external trigger after Escape", async () => {
  const container = document.createElement("div");
  document.body.append(container);
  const root = await createLobeTestRoot(container);
  let setBusy!: (busy: boolean) => void;
  let confirmed = 0;
  let cancelled = 0;
  function Harness() {
    const [open, setOpen] = useState(false);
    const [busy, updateBusy] = useState(false);
    setBusy = updateBusy;
    return createElement("div", null,
      createElement("button", { onClick: () => setOpen(true) }, "Open confirmation"),
      createElement(ConfirmDialog, {
        isOpen: open, loading: busy, title: "Synthetic confirmation", message: "Synthetic action",
        onConfirm: () => { confirmed++; updateBusy(true); },
        onCancel: () => { cancelled++; setOpen(false); },
      }),
    );
  }
  const escape = async () => act(async () => {
    document.activeElement?.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  });
  try {
    await act(async () => root.render(createElement(Harness)));
    const trigger = container.querySelector("button")!;
    await act(async () => { trigger.focus(); trigger.click(); });
    const dialog = container.querySelector<HTMLElement>('[role="dialog"]');
    assert.ok(dialog, "library portal stays inside the theme host");
    assert.ok(dialog.contains(document.activeElement));
    const buttons = dialog.querySelectorAll<HTMLButtonElement>("button");
    await act(async () => buttons[1].click());
    assert.equal(confirmed, 1);
    assert.ok(buttons[0].disabled && buttons[1].disabled);
    await escape();
    assert.equal(cancelled, 0);
    assert.ok(container.querySelector('[role="dialog"]'));
    await act(async () => setBusy(false));
    await escape();
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 300)); });
    assert.equal(container.querySelector('[role="dialog"]'), null);
    assert.equal(cancelled, 1);
    assert.ok(document.activeElement === trigger, "Escape returns focus to the external opener");
  } finally {
    await act(async () => root.unmount());
    container.remove();
  }
});
