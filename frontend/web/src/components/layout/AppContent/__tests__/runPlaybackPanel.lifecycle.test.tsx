import assert from "node:assert/strict";
import test from "node:test";

type Listener = (event: { type: string; [key: string]: unknown }) => void;

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
}

class TestNode extends TestEventTarget {
  parentNode: TestNode | null = null;
  childNodes: TestNode[] = [];
  nodeValue: string | null = null;
  textContent = "";

  get firstChild() {
    return this.childNodes[0] || null;
  }

  get lastChild() {
    return this.childNodes[this.childNodes.length - 1] || null;
  }

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

  contains(node: TestNode | null): boolean {
    return node === this || this.childNodes.some((child) => child.contains(node));
  }
}

class TestElement extends TestNode {
  readonly nodeType = 1;
  readonly namespaceURI = "http://www.w3.org/1999/xhtml";
  readonly style = { setProperty() {}, removeProperty() {} };
  readonly attributes = new Map<string, string>();
  ownerDocument!: TestDocument;
  className = "";
  private html = "";

  constructor(readonly tagName: string) {
    super();
  }

  get nodeName() {
    return this.tagName.toUpperCase();
  }

  get innerHTML() {
    return this.html;
  }

  set innerHTML(value: string) {
    this.html = value;
    this.childNodes = [];
    if (value) this.appendChild(this.ownerDocument.createTextNode(value));
  }

  setAttribute(name: string, value: string) {
    this.attributes.set(name, value);
  }

  removeAttribute(name: string) {
    this.attributes.delete(name);
  }

  getAttribute(name: string) {
    return this.attributes.get(name) || null;
  }

  hasAttribute(name: string) {
    return this.attributes.has(name);
  }
}

class TestText extends TestNode {
  readonly nodeType = 3;
  readonly nodeName = "#text";
  ownerDocument!: TestDocument;

  constructor(value: string) {
    super();
    this.nodeValue = value;
    this.textContent = value;
  }

  get data() {
    return this.nodeValue || "";
  }

  set data(value: string) {
    this.nodeValue = value;
    this.textContent = value;
  }
}

class TestDocument extends TestNode {
  readonly nodeType = 9;
  readonly nodeName = "#document";
  readonly documentElement: TestElement;
  readonly head: TestElement;
  readonly body: TestElement;
  activeElement: TestElement;
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

function installDom() {
  const document = new TestDocument();
  const windowTarget = new TestEventTarget() as TestEventTarget & {
    document: TestDocument;
    location: { href: string };
    setTimeout: typeof setTimeout;
    clearTimeout: typeof clearTimeout;
  };
  windowTarget.document = document;
  windowTarget.location = { href: "http://test.local/" };
  windowTarget.setTimeout = setTimeout;
  windowTarget.clearTimeout = clearTimeout;
  Object.assign(windowTarget, {
    Element: TestElement,
    HTMLElement: TestElement,
    HTMLIFrameElement: TestElement,
    Node: TestNode,
  });
  document.defaultView = windowTarget as unknown as typeof window;
  Object.assign(globalThis, {
    window: windowTarget,
    document,
    Node: TestNode,
    Element: TestElement,
    HTMLElement: TestElement,
    HTMLIFrameElement: TestElement,
    SVGElement: TestElement,
    IS_REACT_ACT_ENVIRONMENT: true,
  });
  return document;
}

const document = installDom();

test("mounted RunPlaybackPanel ignores an aborted stale owner read after run replacement", async () => {
  const React = await import("react");
  const { createRoot } = await import("react-dom/client");
  const { RunPlaybackPanel } = await import("../RunPlaybackPanel.tsx");
  const { RunControlLifecycle } = await import(
    "../../../../hooks/useAgent/runControlLifecycle.ts"
  );
  const lifecycle = new RunControlLifecycle();
  lifecycle.configure({
    adoptRunControlChild: async () => "superseded",
    reconnectRunControlOwner: async () => {},
  });
  lifecycle.bindParent({
    chatHistoryGeneration: 1,
    authRevision: 1,
    auth: {
      incarnation: "incarnation-a",
      sessionMarker: "marker-a",
      tenantId: "tenant-a",
      userId: "user-a",
      roles: ["member"],
      permissions: ["chat:write"],
      isAdmin: false,
      isActive: true,
    },
    sessionId: "session-a",
    runId: "run-a",
  });

  const originalFetch = globalThis.fetch;
  let resolvePlayback!: (response: Response) => void;
  let oldSignal: AbortSignal | null = null;
  globalThis.fetch = (async (input, options) => {
    const url = String(input);
    if (url.includes("/runs/run-a/playback")) {
      oldSignal = options?.signal as AbortSignal;
      return new Promise<Response>((resolve) => {
        resolvePlayback = resolve;
      });
    }
    if (url.includes("/sessions/session-a/status")) {
      return new Response(
        JSON.stringify({ session_id: "session-a", run_id: "run-a", status: "running" }),
      );
    }
    throw new Error(`unexpected request: ${url}`);
  }) as typeof fetch;

  const container = document.createElement("div");
  const root = createRoot(container as never);
  try {
    await React.act(async () => {
      root.render(
        React.createElement(RunPlaybackPanel, {
          lifecycle,
          panelKey: "run-playback:run-a",
        }),
      );
    });
    await React.act(async () => {
      lifecycle.open();
      await Promise.resolve();
    });
    assert.ok(oldSignal, "mounted panel should start the lifecycle GET");

    await React.act(async () => {
      lifecycle.bindParent({
        chatHistoryGeneration: 2,
        authRevision: 1,
        auth: {
          incarnation: "incarnation-a",
          sessionMarker: "marker-a",
          tenantId: "tenant-a",
          userId: "user-a",
          roles: ["member"],
          permissions: ["chat:write"],
          isAdmin: false,
          isActive: true,
        },
        sessionId: "session-b",
        runId: "run-b",
      });
    });
    assert.equal((oldSignal as AbortSignal).aborted, true);

    resolvePlayback(
      new Response(
        JSON.stringify({ run_id: "run-a", timeline: [], events: [], artifacts: [], steps: [], multi_agent: null }),
      ),
    );
    await React.act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    const snapshot = lifecycle.getSnapshot();
    assert.equal(snapshot.owner?.runId, "run-b");
    assert.equal(snapshot.playback, null, "stale run A must not publish over run B");
    const panel = container.firstChild as TestElement;
    assert.equal(panel.getAttribute("aria-busy"), "false");
  } finally {
    globalThis.fetch = originalFetch;
    await React.act(async () => root.unmount());
  }
});

test("mounted RunPlaybackPanel rejects a late read after marker and role rotation", async () => {
  const React = await import("react");
  const { createRoot } = await import("react-dom/client");
  const { RunPlaybackPanel } = await import("../RunPlaybackPanel.tsx");
  const { RunControlLifecycle } = await import(
    "../../../../hooks/useAgent/runControlLifecycle.ts"
  );
  const lifecycle = new RunControlLifecycle();
  lifecycle.configure({
    adoptRunControlChild: async () => "superseded",
    reconnectRunControlOwner: async () => {},
  });
  lifecycle.bindParent({
    chatHistoryGeneration: 1,
    authRevision: 1,
    auth: {
      incarnation: "incarnation-a",
      sessionMarker: "marker-a",
      tenantId: "tenant-a",
      userId: "user-a",
      roles: ["member"],
      permissions: ["chat:write"],
      isAdmin: false,
      isActive: true,
    },
    sessionId: "session-a",
    runId: "run-a",
  });
  const originalFetch = globalThis.fetch;
  let resolvePlayback!: (response: Response) => void;
  let signal: AbortSignal | null = null;
  globalThis.fetch = ((_input, options) => {
    signal = options?.signal as AbortSignal;
    return new Promise<Response>((resolve) => {
      resolvePlayback = resolve;
    });
  }) as typeof fetch;
  const container = document.createElement("div");
  const root = createRoot(container as never);

  try {
    await React.act(async () => {
      root.render(React.createElement(RunPlaybackPanel, { lifecycle, panelKey: "run-playback:run-a" }));
      lifecycle.open();
      await Promise.resolve();
    });
    assert.ok(signal);
    await React.act(async () => {
      lifecycle.bindParent({
        chatHistoryGeneration: 1,
        authRevision: 2,
        auth: {
          incarnation: "incarnation-b",
          sessionMarker: "marker-b",
          tenantId: "tenant-a",
          userId: "user-a",
          roles: ["reviewer"],
          permissions: ["chat:read"],
          isAdmin: false,
          isActive: true,
        },
        sessionId: "session-a",
        runId: "run-a",
      });
    });
    assert.equal((signal as AbortSignal).aborted, true);
    resolvePlayback(
      new Response(
        JSON.stringify({ run_id: "run-a", timeline: [], events: [], artifacts: [], steps: [], multi_agent: null }),
      ),
    );
    await React.act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    assert.equal(lifecycle.getSnapshot().owner?.auth.sessionMarker, "marker-b");
    assert.equal(lifecycle.getSnapshot().playback, null);
  } finally {
    globalThis.fetch = originalFetch;
    await React.act(async () => root.unmount());
  }
});
