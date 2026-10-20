// @ts-expect-error jsdom has no local type declarations.
import { JSDOM } from "jsdom";

/** Real DOM for library controls; layout and media preferences are synthetic. */
export function installSelectTestDom() {
  const dom = new JSDOM("<!doctype html><html><body></body></html>", {
    url: "http://localhost",
    pretendToBeVisual: true,
  });
  const getStyle = dom.window.getComputedStyle.bind(dom.window);
  dom.window.getComputedStyle = (element: Element) => getStyle(element);
  dom.window.PointerEvent = class extends dom.window.MouseEvent {
    readonly pointerId: number;
    readonly pointerType: string;
    readonly isPrimary: boolean;
    constructor(type: string, init: PointerEventInit = {}) {
      super(type, init);
      this.pointerId = init.pointerId ?? 0;
      this.pointerType = init.pointerType ?? "";
      this.isPrimary = init.isPrimary ?? false;
    }
  };
  dom.window.matchMedia = (media: string) => ({
    matches: false, media, onchange: null,
    addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; },
  });
  dom.window.ResizeObserver = class {
    observe() {} unobserve() {} disconnect() {}
  };
  for (const name of [
    "window", "Window", "document", "navigator", "localStorage", "sessionStorage", "Node", "NodeFilter", "Element", "HTMLElement",
    "HTMLInputElement", "HTMLButtonElement", "HTMLTextAreaElement", "HTMLSelectElement", "HTMLFormElement", "HTMLIFrameElement", "HTMLAnchorElement", "SVGElement", "DocumentFragment", "ShadowRoot", "DOMRect", "DOMRectReadOnly",
    "Event", "InputEvent", "CompositionEvent", "CustomEvent", "KeyboardEvent", "MouseEvent", "PointerEvent", "MutationObserver",
    "getComputedStyle", "matchMedia", "ResizeObserver", "requestAnimationFrame", "cancelAnimationFrame",
  ]) {
    Object.defineProperty(globalThis, name, {
      configurable: true,
      writable: true,
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
