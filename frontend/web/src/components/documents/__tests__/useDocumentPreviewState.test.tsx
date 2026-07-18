import assert from "node:assert/strict";
import test from "node:test";

type Listener = (event: { type: string }) => void;

class TestEventTarget {
  private readonly listeners = new Map<string, Set<Listener>>();

  addEventListener(type: string, listener: Listener) {
    const listeners = this.listeners.get(type) || new Set<Listener>();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type: string, listener: Listener) {
    this.listeners.get(type)?.delete(listener);
  }

  dispatchEvent(event: { type: string }) {
    this.listeners.get(event.type)?.forEach((listener) => listener(event));
    return true;
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
}

class TestElement extends TestNode {
  readonly nodeType = 1;
  readonly namespaceURI = "http://www.w3.org/1999/xhtml";
  readonly style: Record<string, string> = {};
  ownerDocument!: TestDocument;

  constructor(readonly tagName: string) {
    super();
  }

  get nodeName() {
    return this.tagName.toUpperCase();
  }

  setAttribute() {}
  removeAttribute() {}
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
  defaultView: typeof window | null = null;

  constructor() {
    super();
    this.documentElement = this.createElement("html");
    this.head = this.createElement("head");
    this.body = this.createElement("body");
    this.documentElement.appendChild(this.head);
    this.documentElement.appendChild(this.body);
    this.appendChild(this.documentElement);
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
  innerWidth: number;
  location: { origin: string };
  matchMedia: () => MediaQueryList;
};
windowTarget.document = document;
windowTarget.innerWidth = 1024;
windowTarget.location = { origin: "http://localhost" };
windowTarget.matchMedia = () =>
  ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }) as unknown as MediaQueryList;
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

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

function xlsxDto(value: string): string {
  return JSON.stringify({
    schema_version: "ai-platform.file-preview.v1",
    kind: "xlsx_table",
    status: "ready",
    content: {
      sheet_count: 1,
      sheets: [
        {
          name: "Checks",
          rows: [{ row: 1, cells: [{ column: 1, kind: "text", value }] }],
        },
      ],
    },
    truncated: false,
    warnings: [],
    error: null,
  });
}

test("mounted XLSX preview keeps B after deferred A resolves, retries safely, and never fetches download", async () => {
  const React = await import("react");
  const { createRoot } = await import("react-dom/client");
  const { useDocumentPreviewState } = await import("../useDocumentPreviewState.ts");
  const pending = new Map<string, ReturnType<typeof deferred<Response>>>();
  const requests: string[] = [];
  const originalFetch = globalThis.fetch;
  const originalConsoleError = console.error;
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = String(input);
    requests.push(url);
    const request = pending.get(url);
    assert.ok(request, `unexpected request: ${url}`);
    return request.promise;
  }) as typeof fetch;
  console.error = () => {};

  type Snapshot = {
    data: { content: string } | null;
    loading: boolean;
    error: string | null;
  };
  let snapshot: Snapshot | null = null;
  const container = document.createElement("div");
  const root = createRoot(container as never);
  const baseProps = {
    content: undefined,
    s3Key: undefined,
    mimeType:
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    onClose: () => {},
  };
  const propsFor = (id: string) => ({
    ...baseProps,
    path: `${id}.xlsx`,
    previewUrl: `/api/ai/artifacts/${id}/preview`,
    downloadUrl: `/api/ai/artifacts/${id}/download`,
  });

  function Probe({ props }: { props: ReturnType<typeof propsFor> }) {
    snapshot = useDocumentPreviewState(props) as Snapshot;
    return React.createElement("div");
  }

  async function render(props: ReturnType<typeof propsFor>) {
    await React.act(async () => {
      root.render(React.createElement(Probe, { props }));
      await Promise.resolve();
      await Promise.resolve();
    });
  }

  async function flush() {
    await React.act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
  }

  function currentSnapshot(): Snapshot {
    assert.ok(snapshot);
    return snapshot as Snapshot;
  }

  try {
    const a = propsFor("workbook-a");
    const b = propsFor("workbook-b");
    const retry = propsFor("workbook-retry");
    const unmounted = propsFor("workbook-unmounted");
    for (const props of [a, b, retry, unmounted]) {
      pending.set(props.previewUrl, deferred<Response>());
    }

    await render(a);
    await render(b);
    assert.equal(currentSnapshot().data, null);
    assert.equal(currentSnapshot().loading, true);

    pending.get(b.previewUrl)?.resolve(
      new Response(xlsxDto("B"), {
        headers: { "content-type": "application/json" },
      }),
    );
    await flush();
    assert.equal(currentSnapshot().data?.content, xlsxDto("B"));
    assert.equal(currentSnapshot().loading, false);

    pending.get(a.previewUrl)?.resolve(
      new Response(xlsxDto("A"), {
        headers: { "content-type": "application/json" },
      }),
    );
    await flush();
    assert.equal(currentSnapshot().data?.content, xlsxDto("B"));

    await render(retry);
    pending.get(retry.previewUrl)?.resolve(new Response("failed", { status: 500 }));
    await flush();
    assert.notEqual(currentSnapshot().error, null);

    const recovered = propsFor("workbook-recovered");
    pending.set(recovered.previewUrl, deferred<Response>());
    await render(recovered);
    assert.equal(currentSnapshot().error, null);
    pending.get(recovered.previewUrl)?.resolve(
      new Response(xlsxDto("Recovered"), {
        headers: { "content-type": "application/json" },
      }),
    );
    await flush();
    assert.equal(currentSnapshot().data?.content, xlsxDto("Recovered"));

    await render(unmounted);
    await React.act(async () => root.unmount());
    pending.get(unmounted.previewUrl)?.resolve(
      new Response(xlsxDto("Unmounted"), {
        headers: { "content-type": "application/json" },
      }),
    );
    await Promise.resolve();
    await Promise.resolve();

    assert.ok(requests.every((url) => url.endsWith("/preview")));
    assert.ok(requests.includes(a.previewUrl));
    assert.ok(requests.includes(b.previewUrl));
  } finally {
    console.error = originalConsoleError;
    globalThis.fetch = originalFetch;
  }
});
