import assert from "node:assert/strict";
import test from "node:test";

import ExcelPreview from "../ExcelPreview.tsx";

import { installSelectTestDom } from "../../../common/__tests__/selectTestDom";
import { createLobeTestRoot } from "../../../common/__tests__/lobeUiTestRoot";
const environment = installSelectTestDom();

function previewDto() {
  return JSON.stringify({
    schema_version: "ai-platform.file-preview.v2",
    kind: "xlsx_table",
    status: "ready",
    content: {
      sheet_count: 2,
      sheets: [
        { name: "First", rows: [], images: [] },
        { name: "Second", rows: [], images: [] },
      ],
    },
    truncated: false,
    warnings: [],
    error: null,
  });
}

test("mounted tabs use instance-unique ids and move focus with Arrow/Home/End", async () => {
  const React = await import("react");
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = await createLobeTestRoot(container);
  const t = (_key: string, options?: Record<string, unknown>) =>
    String(options?.defaultValue ?? "translated");

  try {
    await React.act(async () => {
      root.render(
        React.createElement(
          React.Fragment,
          null,
          React.createElement(ExcelPreview, { previewJson: previewDto(), t }),
          React.createElement(ExcelPreview, { previewJson: previewDto(), t }),
        ),
      );
      await Promise.resolve();
    });

    const tabs = [...container.querySelectorAll<HTMLElement>('[role="tab"]')];
    assert.equal(tabs.length, 4);
    const ids = tabs.map((tab) => tab.getAttribute("id"));
    assert.equal(new Set(ids).size, ids.length);

    await React.act(async () => {
      tabs[0].focus();
      tabs[0].dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true, cancelable: true }));
      await Promise.resolve();
    });
    assert.equal(document.activeElement, tabs[1]);
    assert.equal(tabs[1].getAttribute("aria-selected"), "true");

    await React.act(async () => {
      tabs[1].dispatchEvent(new KeyboardEvent("keydown", { key: "Home", bubbles: true, cancelable: true }));
      await Promise.resolve();
    });
    assert.equal(document.activeElement, tabs[0]);

    await React.act(async () => {
      tabs[0].dispatchEvent(new KeyboardEvent("keydown", { key: "End", bubbles: true, cancelable: true }));
      await Promise.resolve();
    });
    assert.equal(document.activeElement, tabs[1]);
  } finally {
    await React.act(async () => root.unmount());
    document.body.removeChild(container);
    environment.close();
  }
});
