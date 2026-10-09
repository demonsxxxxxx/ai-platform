import assert from "node:assert/strict";
import test from "node:test";
// jsdom 26 ships no declarations; this mounted test uses only its runtime constructor.
// @ts-expect-error jsdom is the pinned mounted-test runtime.
import { JSDOM } from "jsdom";
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import type { MessagePart, TextPart } from "../../../../types";
import "../../../../i18n";
import { MessageWorkActivity } from "../MessageWorkActivity";

const workPart = (content: string): MessagePart => ({
  type: "text",
  content,
  logical_id: "part-work-1",
  public_part_id: "part-work-1",
  text_role: "work",
});

const answerPart = (content: string): TextPart => ({
  type: "text",
  content,
  logical_id: "part-answer-1",
  public_part_id: "part-answer-1",
  text_role: "answer",
});

function renderActivity(
  root: ReturnType<typeof createRoot>,
  messageId: string,
  parts: MessagePart[],
) {
  root.render(
    createElement(MessageWorkActivity, {
      messageId,
      isStreaming: true,
      parts,
      partKeys: parts.map((part, index) => `${messageId}:${part.type}:${index}`),
      renderPart: (part) =>
        createElement("span", { "data-part-role": part.type === "text" ? part.text_role : "other" },
          part.type === "text" ? part.content : part.type,
        ),
    }),
  );
}

test("mounted work folding follows source phase while preserving manual choice within it", () => {
  const dom = new JSDOM("<!doctype html><html><body><div id=\"root\"></div></body></html>", {
    url: "http://localhost/",
  });
  for (const key of ["window", "document", "navigator", "HTMLElement", "Node"] as const) {
    Object.defineProperty(globalThis, key, {
      configurable: true,
      value: dom.window[key],
    });
  }
  Object.defineProperty(globalThis, "IS_REACT_ACT_ENVIRONMENT", {
    configurable: true,
    value: true,
  });
  const container = dom.window.document.getElementById("root") as HTMLDivElement;
  const root = createRoot(container);

  try {
    act(() => renderActivity(root, "message-1", [workPart("Checking sources.")]));
    const toggle = container.querySelector<HTMLButtonElement>("[data-message-work-details-toggle]");
    assert.ok(toggle);
    assert.equal(toggle.getAttribute("aria-expanded"), "true");

    act(() => renderActivity(root, "message-1", [
      workPart("Checking sources."),
      { ...answerPart("The answer starts"), text_role: "pending" as const },
    ]));
    assert.equal(toggle.getAttribute("aria-expanded"), "false");
    assert.equal(
      container.querySelector<HTMLElement>("[data-part-role=work]")?.parentElement?.hidden,
      true,
    );

    act(() => toggle.click());
    assert.equal(toggle.getAttribute("aria-expanded"), "true");
    act(() => renderActivity(root, "message-1", [
      workPart("Checking sources."),
      { ...answerPart("The answer starts and continues."), text_role: "pending" as const },
    ]));
    assert.equal(toggle.getAttribute("aria-expanded"), "true");

    act(() => renderActivity(root, "message-1", [
      workPart("Checking sources."),
      answerPart("The answer is ready."),
      workPart("Verifying the final detail."),
    ]));
    assert.equal(toggle.getAttribute("aria-expanded"), "true");

    act(() => toggle.click());
    assert.equal(toggle.getAttribute("aria-expanded"), "false");
    act(() => renderActivity(root, "message-1", [
      workPart("Checking sources."),
      answerPart("The answer is ready."),
      workPart("Verifying the final detail."),
    ]));
    assert.equal(toggle.getAttribute("aria-expanded"), "false");
  } finally {
    act(() => root.unmount());
    dom.window.close();
  }
});
