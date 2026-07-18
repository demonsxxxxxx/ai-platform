import assert from "node:assert/strict";
import test from "node:test";

import ExcelPreview from "../ExcelPreview.tsx";

type TestEvent = {
  type: string;
  key?: string;
  bubbles?: boolean;
  target: TestNode;
  defaultPrevented?: boolean;
  preventDefault: () => void;
};
type Listener = (event: TestEvent) => void;

class TestEventTarget {
  protected readonly listeners = new Map<string, Set<Listener>>();

  addEventListener(type: string, listener: Listener) {
    const listeners = this.listeners.get(type) || new Set<Listener>();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type: string, listener: Listener) {
    this.listeners.get(type)?.delete(listener);
  }
}

class TestNode extends TestEventTarget {
  parentNode: TestNode | null = null;
  childNodes: TestNode[] = [];
  textContent = "";

  appendChild(child: TestNode) {
    child.parentNode = this;
    this.childNodes.push(child);
    return child;
  }

  insertBefore(child: TestNode, reference: TestNode | null) {
    child.parentNode = this;
    const index = reference ? this.childNodes.indexOf(reference) : -1;
    if (index < 0) this.childNodes.push(child);
    else this.childNodes.splice(index, 0, child);
    return child;
  }

  removeChild(child: TestNode) {
    const index = this.childNodes.indexOf(child);
    if (index >= 0) this.childNodes.splice(index, 1);
    child.parentNode = null;
    return child;
  }

  dispatchEvent(event: TestEvent) {
    this.listeners.get(event.type)?.forEach((listener) => listener(event));
    if (event.bubbles) this.parentNode?.dispatchBubbledEvent(event);
    return true;
  }

  private dispatchBubbledEvent(event: TestEvent) {
    this.listeners.get(event.type)?.forEach((listener) => listener(event));
    this.parentNode?.dispatchBubbledEvent(event);
  }
}

class TestElement extends TestNode {
  readonly nodeType = 1;
  readonly namespaceURI = "http://www.w3.org/1999/xhtml";
  readonly style: Record<string, string> = {};
  readonly attributes = new Map<string, string>();
  ownerDocument!: TestDocument;

  constructor(readonly tagName: string) {
    super();
  }

  get nodeName() {
    return this.tagName.toUpperCase();
  }

  setAttribute(name: string, value: string) {
    this.attributes.set(name, value);
  }

  getAttribute(name: string) {
    return this.attributes.get(name) ?? null;
  }

  removeAttribute(name: string) {
    this.attributes.delete(name);
  }

  focus() {
    this.ownerDocument.activeElement = this;
  }
}

class TestText extends TestNode {
  readonly nodeType = 3;
  readonly nodeName = "#text";
  ownerDocument!: TestDocument;

  constructor(value: string) {
    super();
    this.textContent = value;
  }
}

class TestDocument extends TestNode {
  readonly nodeType = 9;
  readonly nodeName = "#document";
  readonly documentElement: TestElement;
  readonly head: TestElement;
  readonly body: TestElement;
  activeElement: TestElement | null = null;
  defaultView: typeof window | null = null;

  constructor() {
    super();
    this.documentElement = this.createElement("html");
    this.head = this.createElement("head");
    this.body = this.createElement("body");
    this.documentElement.appendChild(this.head);
    this.documentElement.appendChild(this.body);
    this.appendChild(this.documentElement);
    this.activeElement = this.body;
  }

  createElement(tagName: string) {
    const element = new TestElement(tagName);
    element.ownerDocument = this;
    return element;
  }

  createElementNS(_namespace: string, tagName: string) {
    return this.createElement(tagName);
  }

  createTextNode(value: string) {
    const text = new TestText(value);
    text.ownerDocument = this;
    return text;
  }
}

const document = new TestDocument();
const windowTarget = new TestEventTarget() as TestEventTarget & {
  document: TestDocument;
  location: { origin: string };
};
windowTarget.document = document;
windowTarget.location = { origin: "http://localhost" };
document.defaultView = windowTarget as unknown as typeof window;
Object.assign(windowTarget, {
  Node: TestNode,
  Element: TestElement,
  HTMLElement: TestElement,
  HTMLIFrameElement: class TestIFrameElement extends TestElement {},
});
Object.assign(globalThis, {
  window: windowTarget,
  document,
  Node: TestNode,
  Element: TestElement,
  HTMLElement: TestElement,
  HTMLIFrameElement: class TestIFrameElement extends TestElement {},
  SVGElement: TestElement,
  ResizeObserver: class {
    observe() {}
    disconnect() {}
  },
  IS_REACT_ACT_ENVIRONMENT: true,
});
Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: { userAgent: "node" },
});

function previewDto() {
  return JSON.stringify({
    schema_version: "ai-platform.file-preview.v1",
    kind: "xlsx_table",
    status: "ready",
    content: {
      sheet_count: 2,
      sheets: [
        { name: "First", rows: [] },
        { name: "Second", rows: [] },
      ],
    },
    truncated: false,
    warnings: [],
    error: null,
  });
}

function descendants(root: TestNode): TestElement[] {
  const found: TestElement[] = [];
  for (const child of root.childNodes) {
    if (child instanceof TestElement) {
      found.push(child, ...descendants(child));
    }
  }
  return found;
}

test("mounted tabs use instance-unique ids and move focus with Arrow/Home/End", async () => {
  const React = await import("react");
  const { createRoot } = await import("react-dom/client");
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container as never);
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

    const tabs = descendants(container).filter(
      (element) => element.getAttribute("role") === "tab",
    );
    assert.equal(tabs.length, 4);
    const ids = tabs.map((tab) => tab.getAttribute("id"));
    assert.equal(new Set(ids).size, ids.length);

    await React.act(async () => {
      tabs[0].focus();
      tabs[0].dispatchEvent({
        type: "keydown",
        key: "ArrowRight",
        bubbles: true,
        target: tabs[0],
        preventDefault() {},
      });
      await Promise.resolve();
    });
    assert.equal(document.activeElement, tabs[1]);
    assert.equal(tabs[1].getAttribute("aria-selected"), "true");

    await React.act(async () => {
      tabs[1].dispatchEvent({
        type: "keydown",
        key: "Home",
        bubbles: true,
        target: tabs[1],
        preventDefault() {},
      });
      await Promise.resolve();
    });
    assert.equal(document.activeElement, tabs[0]);

    await React.act(async () => {
      tabs[0].dispatchEvent({
        type: "keydown",
        key: "End",
        bubbles: true,
        target: tabs[0],
        preventDefault() {},
      });
      await Promise.resolve();
    });
    assert.equal(document.activeElement, tabs[1]);
  } finally {
    await React.act(async () => root.unmount());
    document.body.removeChild(container);
  }
});
