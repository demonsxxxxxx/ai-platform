import test, { after } from "node:test";
import assert from "node:assert/strict";
import { installSelectTestDom } from "../../common/__tests__/selectTestDom";
import { createLobeTestRoot } from "../../common/__tests__/lobeUiTestRoot";

const dom = installSelectTestDom();
after(() => dom.close());
const { act, createElement, useState } = await import("react");
const { PasswordInput } = await import("../PasswordInput");

test("password visibility keeps the value, native label and localized keyboard-focusable toggle", async () => {
  const container = document.createElement("div");
  const root = await createLobeTestRoot(container);
  function Probe() {
    const [value, setValue] = useState("");
    return createElement("form", null,
      createElement("label", { htmlFor: "synthetic-password" }, "密码"),
      createElement(PasswordInput, {
        id: "synthetic-password", value, onChange: (event) => setValue(event.target.value),
        showPasswordLabel: "显示密码", hidePasswordLabel: "隐藏密码",
      }));
  }
  try {
    await act(async () => root.render(createElement(Probe)));
    const field = container.querySelector("input")!;
    assert.equal(field.type, "password");
    assert.equal(field.labels?.[0]?.textContent, "密码");
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(field, "synthetic-only");
      field.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const toggle = container.querySelector<HTMLButtonElement>('button[aria-label="显示密码"]')!;
    assert.ok(toggle.tabIndex >= 0);
    await act(async () => { toggle.click(); });
    assert.equal(field.type, "text");
    assert.equal(field.value, "synthetic-only");
    assert.equal(toggle.getAttribute("aria-label"), "隐藏密码");
    await act(async () => { toggle.click(); });
    assert.equal(field.type, "password");
    assert.equal(field.value, "synthetic-only");
  } finally { await act(async () => root.unmount()); }
});
