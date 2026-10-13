// @ts-expect-error jsdom has no local type declarations.
import { JSDOM } from "jsdom";

/** Native DOM required by Radix's form, focus, pointer and positioning behavior. */
export function installSelectTestDom() {
  const dom = new JSDOM("<!doctype html><html><body></body></html>", {
    url: "http://localhost",
    pretendToBeVisual: true,
  });
  for (const name of [
    "window", "document", "navigator", "Node", "Element", "HTMLElement",
    "HTMLInputElement", "HTMLTextAreaElement", "HTMLSelectElement", "HTMLFormElement", "HTMLIFrameElement", "SVGElement", "DocumentFragment",
    "Event", "CustomEvent", "KeyboardEvent", "MouseEvent", "MutationObserver",
    "getComputedStyle", "requestAnimationFrame", "cancelAnimationFrame",
  ]) {
    Object.defineProperty(globalThis, name, {
      configurable: true,
      value: dom.window[name],
    });
  }
  // JSDOM does not implement layout-dependent scrolling.
  dom.window.HTMLElement.prototype.scrollIntoView = () => {};
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  return {
    document: dom.window.document as Document,
    FormData: dom.window.FormData as typeof FormData,
    close: () => dom.window.close(),
  };
}
