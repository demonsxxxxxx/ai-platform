import assert from "node:assert/strict";
import test from "node:test";
import { act } from "react";
import { installSelectTestDom } from "./selectTestDom";

const fixture = installSelectTestDom();
test.after(() => fixture.close());
const { createRoot } = await import("react-dom/client");
const { ErrorBoundary } = await import("../ErrorBoundary");
const { default: i18n } = await import("../../../i18n");

test("the application recovery action renders after the UI provider is gone", async () => {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  const originalError = console.error;
  console.error = () => {};
  function BrokenProvider(): never { throw new Error("synthetic provider failure"); }
  try {
    await act(async () => root.render(<ErrorBoundary><BrokenProvider /></ErrorBoundary>));
    const button = container.querySelector("button");
    assert.ok(button, "recovery must remain available without Lobe providers");
    assert.match(button.textContent ?? "", new RegExp(i18n.t("errorBoundary.reloadPage")));
    assert.equal(button.type, "button");
    assert.equal(button.disabled, false);
    assert.match(container.textContent ?? "", /synthetic provider failure/);
  } finally {
    await act(async () => root.unmount());
    console.error = originalError;
    container.remove();
  }
});
