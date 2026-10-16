import assert from "node:assert/strict";
import test from "node:test";
import { act } from "react";
import { createLobeTestRoot } from "../../../common/__tests__/lobeUiTestRoot";
import { installSelectTestDom } from "../../../common/__tests__/selectTestDom";
import { createSyntheticWordDocument } from "./wordPreviewFixture.ts";
import i18n from "../../../../i18n";

const t = (key: string) => key;

test("Word uses measured direct DOM pages and retains zoom, gestures, selection, and reopening", async () => {
  const environment = installSelectTestDom();
  const dom = { window };
  document.body.innerHTML = "<div id='root'></div>";
  const saved = new Map<string, PropertyDescriptor | undefined>();
  const install = (key: string, value: unknown) => {
    saved.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
    Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
  };
  for (const key of ["window", "document", "DOMParser", "XMLSerializer", "Node", "Element", "HTMLElement", "Blob", "FileReader", "MutationObserver"] as const) install(key, dom.window[key]);
  install("IS_REACT_ACT_ENVIRONMENT", true);
  install("requestAnimationFrame", (callback: FrameRequestCallback) => setTimeout(callback, 0));
  install("ResizeObserver", class { observe() {} disconnect() {} });
  // jsdom has no layout engine. Model geometry from the real renderer's page
  // styles and verify React propagates measured values instead of Letter constants.
  Object.defineProperty(dom.window.HTMLElement.prototype, "clientWidth", { get: () => 1240 });
  Object.defineProperty(dom.window.HTMLElement.prototype, "offsetWidth", { get() {
    return this.matches("section.docx") ? parseFloat(this.style.width) * 4 / 3 : 0;
  } });
  Object.defineProperty(dom.window.HTMLElement.prototype, "scrollHeight", { get() {
    return this.matches(".docx-wrapper")
      ? [...this.querySelectorAll("section.docx")].reduce((sum, page) => sum + parseFloat((page as HTMLElement).style.minHeight) * 4 / 3, 0)
      : 0;
  } });
  const host = document.getElementById("root")!;
  const root = await createLobeTestRoot(host);
  const { default: WordPreview } = await import("../WordPreview.tsx");
  const ready = async () => {
    for (let attempt = 0; attempt < 100; attempt++) {
      await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
      if (host.querySelector("section.docx") && host.querySelector("button")) return;
    }
    assert.fail(`Word conversion did not finish: ${host.textContent}`);
  };
  try {
    const bytes = await createSyntheticWordDocument({ landscape: true });
    await act(async () => root.render(<WordPreview arrayBuffer={bytes} t={t} />));
    await ready();
    const content = host.querySelector<HTMLElement>(".docx-preview-content")!;
    const pages = content.querySelectorAll("section.docx");
    assert.equal(pages.length, 2);
    assert.match(content.textContent!, /Final landscape page/);
    assert.equal(host.querySelector("iframe"), null, "Word must not intercept gestures in an iframe");
    assert.equal(content.style.width, "1056px");
    assert.equal(content.style.height, "1632px");
    assert.equal(content.style.transform, "scale(1)");
    assert.ok(content.querySelector("img[src^='data:']"));
    assert.ok(content.querySelector("math"));
    assert.ok(content.querySelector('a[href="https://synthetic.invalid/link"]'));
    const selection = dom.window.getSelection()!;
    const range = document.createRange();
    range.selectNodeContents(pages[1]);
    selection.removeAllRanges(); selection.addRange(range);
    assert.match(selection.toString(), /Final landscape page/);
    const click = async (label: string) => {
      const button = host.querySelector<HTMLButtonElement>(`button[aria-label='${i18n.t(label)}']`)!;
      assert.ok(button);
      await act(async () => button.click());
    };
    await click("imageViewer.zoomIn");
    assert.equal(content.style.transform, "scale(1.2)");
    assert.match(selection.toString(), /Final landscape page/);
    assert.ok(Math.abs(parseFloat(content.parentElement!.style.height) - 1632 * 1.2) < 0.001);
    await click("imageViewer.reset");
    assert.equal(content.style.transform, "scale(1)");
    const viewport = host.querySelector<HTMLElement>("[aria-label='documents.wordPreviewTitle']")!;
    await act(async () => viewport.dispatchEvent(new dom.window.MouseEvent("dblclick", { bubbles: true })));
    assert.equal(content.style.transform, "scale(1.8)");
    await click("imageViewer.reset");
    const touch = async (type: string, points: number[][], changed: number[][] = points) => {
      const event = new dom.window.Event(type, { bubbles: true, cancelable: true });
      const list = (values: number[][]) => values.map(([clientX, clientY]) => ({ clientX, clientY }));
      Object.defineProperties(event, { touches: { value: list(points) }, changedTouches: { value: list(changed) } });
      await act(async () => viewport.dispatchEvent(event));
    };
    await touch("touchstart", [[0, 0], [100, 0]]);
    await touch("touchmove", [[0, 0], [150, 0]]);
    assert.equal(content.style.transform, "scale(1.5)");
    await touch("touchend", [], [[0, 0], [150, 0]]);
    viewport.scrollTop = 100; viewport.scrollLeft = 80;
    await touch("touchstart", [[100, 100]]);
    await touch("touchmove", [[60, 50]]);
    assert.equal(viewport.scrollTop, 150);
    assert.equal(viewport.scrollLeft, 120);
    await touch("touchend", [], [[60, 50]]);
    assert.ok(viewport.classList.contains("overflow-auto"), "native wheel scrolling stays on the document viewport");
    await act(async () => root.render(null));
    assert.equal(host.textContent, "");
    const portrait = await createSyntheticWordDocument();
    await act(async () => root.render(<WordPreview arrayBuffer={portrait} t={t} />));
    await ready();
    const reopened = host.querySelector<HTMLElement>(".docx-preview-content")!;
    assert.equal(reopened.style.width, "816px");
    assert.equal(reopened.style.height, "2112px");
    assert.equal(reopened.style.transform, "scale(1)");
    assert.match(reopened.textContent!, /Final synthetic page/);
    assert.doesNotMatch(reopened.textContent!, /Final landscape page/);
  } finally {
    await act(async () => root.unmount());
    environment.close();
    for (const [key, descriptor] of saved) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else Reflect.deleteProperty(globalThis, key);
    }
  }
});
