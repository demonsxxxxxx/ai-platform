import assert from "node:assert/strict";
import test from "node:test";
import { installSelectTestDom } from "../../../common/__tests__/selectTestDom";
import { act, createElement } from "react";
import { createLobeTestRoot } from "../../../common/__tests__/lobeUiTestRoot";
import type { MessagePart } from "../../../../types";
import "../../../../i18n";
import { MessageWorkActivity } from "../MessageWorkActivity";
import { formatTaskDuration } from "../taskDuration";

test("task duration uses whole h/m/s with minute and hour carries", () => {
  for (const [ms, expected] of [
    [0, "0s"], [999, "0s"], [1250, "1s"], [59_999, "59s"],
    [60_000, "1m0s"], [3_055_000, "50m55s"], [3_599_999, "59m59s"],
    [3_600_000, "1h0m0s"], [4_385_000, "1h13m5s"], [86_400_000, "24h0m0s"],
  ] as const) assert.equal(formatTaskDuration(ms), expected);
  for (const value of [undefined, Number.NaN, Infinity, -1]) {
    assert.equal(formatTaskDuration(value), null);
  }
});

const tool = (id: string): MessagePart => ({
  type: "tool", name: "Read", args: {}, status: "completed",
  public_operation_id: id, public_category: "read",
});
const text = (content: string): MessagePart => ({ type: "text", content });

test("one process archives narration at tool boundaries and retains its manual choice", async () => {
  const environment = installSelectTestDom();
  const container = document.createElement("div");
  const root = await createLobeTestRoot(container);
  const render = (parts: MessagePart[], live = true, runDurationMs?: number) => root.render(createElement(MessageWorkActivity, {
    messageId: "message-1", isStreaming: live, runDurationMs, parts,
    partKeys: parts.map((_, index) => `part-${index}`),
    renderPart: (part, _index, withinWorkDetails) => createElement("span", {
      "data-part": part.type, "data-in-process": withinWorkDetails,
    }, part.type === "text" ? part.content : part.type),
  }));
  try {
    act(() => render([text("Before")]));
    assert.equal(container.querySelector("[data-message-work-details-toggle]"), null);
    assert.equal(container.querySelector("[data-message-result]")?.textContent, "Before");

    act(() => render([text("Before"), tool("first"), text("After")], true, 1250));
    assert.equal(container.querySelector("[data-task-duration]"), null, "streaming must not show a final time");
    const toggle = container.querySelector<HTMLButtonElement>("[data-message-work-details-toggle]")!;
    const detailsId = toggle.getAttribute("aria-controls")!;
    assert.equal(toggle.getAttribute("aria-expanded"), "true");
    assert.equal(container.querySelector("[data-message-result]")?.textContent, "After");
    assert.equal(container.querySelector('[data-in-process="true"][data-part="text"]')?.textContent, "Before");

    act(() => toggle.click());
    act(() => render([text("Before"), tool("first"), text("After and more"), tool("second"), text("Final")]));
    assert.equal(container.querySelectorAll("[data-message-work-details-toggle]").length, 1);
    assert.equal(container.querySelector("[data-message-work-details-toggle]"), toggle);
    assert.equal(toggle.getAttribute("aria-expanded"), "false");
    assert.equal(container.querySelector("[data-message-result]")?.textContent, "Final");
    assert.equal(container.querySelectorAll('[data-in-process="true"][data-part="text"]').length, 2);
    assert.equal(container.querySelector(`#${detailsId}`)?.hasAttribute("hidden"), true);

    act(() => render([text("Before"), tool("first"), text("After and more"), tool("second"), text("Final streamed suffix")], false, 1250));
    assert.equal(toggle.getAttribute("aria-expanded"), "false");
    const duration = container.querySelector("[data-task-duration]");
    assert.equal(duration?.textContent?.trim(), "耗时 1s");
    assert.equal(duration?.closest("[hidden]"), null, "task time stays visible when process is collapsed");
    assert.equal(container.querySelector("[data-message-result]")?.textContent, "Final streamed suffix");
    act(() => toggle.click());
    assert.equal(container.querySelector(`#${detailsId}`)?.hasAttribute("hidden"), false);
    assert.equal(toggle.getAttribute("aria-expanded"), "true");
  } finally {
    act(() => root.unmount());
    environment.close();
  }
});

test("plain answers need no process and artifact results stay outside it", async () => {
  const environment = installSelectTestDom();
  const container = document.createElement("div");
  const root = await createLobeTestRoot(container);
  const render = (parts: MessagePart[], runDurationMs?: number) => root.render(createElement(MessageWorkActivity, {
    messageId: "message-plain", isStreaming: false, runDurationMs, parts,
    partKeys: parts.map((_, index) => `part-${index}`),
    renderPart: part => createElement("span", { "data-part": part.type }, part.type === "text" ? part.content : part.type),
  }));
  try {
    act(() => render([text("Plain answer")]));
    assert.equal(container.querySelector("[data-message-work-details-toggle]"), null);
    assert.equal(container.querySelector("[data-message-result]")?.textContent, "Plain answer");
    assert.equal(container.querySelector("[data-task-duration]"), null);
    act(() => render([text("Plain answer")], 0));
    assert.equal(container.querySelector("[data-task-duration]")?.textContent?.trim(), "耗时 0s");
    assert.equal(container.querySelector("[data-message-work-details-toggle]"), null, "time must not create an empty process");
    for (const [ms, label] of [[3_055_000, "50m55s"], [4_385_000, "1h13m5s"], [3_600_000, "1h0m0s"]] as const) {
      act(() => render([text("Plain answer")], ms));
      assert.equal(container.querySelector("[data-task-duration]")?.textContent?.trim(), `耗时 ${label}`);
    }
    act(() => render([text("Plain answer")], Number.NaN));
    assert.equal(container.querySelector("[data-task-duration]"), null);
    const artifact: MessagePart = {
      type: "artifact", artifact_id: "artifact-1", artifact_type: "file", label: "Report",
      content_type: "text/plain", size_bytes: 6,
    };
    act(() => render([text("Checking"), artifact, tool("first"), text("Report")]));
    assert.equal(container.querySelector("[data-message-work-details-toggle]")?.getAttribute("aria-expanded"), "false");
    assert.equal(container.querySelector("[data-message-result]")?.textContent, "artifactReport");
    assert.equal(container.querySelector('[data-message-result] [data-part="artifact"]')?.closest("[hidden]"), null);
  } finally {
    act(() => root.unmount());
    environment.close();
  }
});
