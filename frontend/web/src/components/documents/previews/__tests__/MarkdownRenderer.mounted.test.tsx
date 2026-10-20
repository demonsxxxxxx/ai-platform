import assert from "node:assert/strict";
import test from "node:test";
import { act } from "react";
import { installSelectTestDom } from "../../../common/__tests__/selectTestDom";

test("Markdown document preview renders safe rich content without a chat runtime", async () => {
  const environment = installSelectTestDom();
  const { createLobeTestRoot } = await import("../../../common/__tests__/lobeUiTestRoot");
  const { default: MarkdownRenderer } = await import("../MarkdownRenderer");
  const container = document.createElement("div");
  const root = await createLobeTestRoot(container);
  try {
    await act(async () => root.render(<MarkdownRenderer _t={(key) => key} content={[
      "# 独立文档", "", "公式：$x^2 + y^2$", "", "| 列 | 值 |", "| --- | --- |", "| A | 1 |", "",
      "安全代码：`/project/config.json`", "", "[不安全链接](javascript:alert%281%29)",
    ].join("\n")} />));
    assert.equal(container.querySelector("h1")?.textContent, "独立文档");
    assert.ok(container.querySelector(".katex"));
    assert.equal(container.querySelectorAll("tbody tr").length, 1);
    assert.match(container.textContent ?? "", /\/project\/config.json/);
    assert.equal(container.querySelector('a[href^="javascript:"]'), null);
    assert.equal(container.querySelector('[data-assistant-ui-projection]'), null);
  } finally {
    await act(async () => root.unmount());
    environment.close();
  }
});
