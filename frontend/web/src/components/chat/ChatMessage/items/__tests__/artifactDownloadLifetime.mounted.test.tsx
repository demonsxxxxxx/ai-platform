import assert from "node:assert/strict";
import test from "node:test";
// @ts-expect-error jsdom is the pinned mounted-test runtime without declarations.
import { JSDOM } from "jsdom";
import { act, createElement, StrictMode } from "react";
import { createRoot } from "react-dom/client";
import type { ArtifactPart } from "../../../../../types";

test("mounted artifact success and retry buttons survive settled TTL without a rerender", async (t) => {
  // Fake time exercises the registry's 30-second policy, not real network latency.
  t.mock.timers.enable({ apis: ["Date", "setTimeout"], now: 1_000 });
  const { MessagePartRenderer } = await import("../../MessagePartRenderer");
  const { createArtifactDownloadScopeContext, createArtifactDownloadScope, clearArtifactDownloadScope } =
    await import("../artifactDownloadRegistry");
  const dom = new JSDOM('<div id="root"></div>', { url: "http://localhost/" });
  const globals = ["window", "document", "navigator", "HTMLElement", "Node", "IS_REACT_ACT_ENVIRONMENT"] as const;
  const saved = globals.map((key) => Object.getOwnPropertyDescriptor(globalThis, key));
  for (const key of globals) {
    Object.defineProperty(globalThis, key, { configurable: true, value: key === "IS_REACT_ACT_ENVIRONMENT" ? true : dom.window[key] });
  }
  t.mock.method(dom.window.HTMLAnchorElement.prototype, "click", () => undefined);
  const root = createRoot(dom.window.document.getElementById("root")!);
  const context = createArtifactDownloadScopeContext({ tenantId: "ttl-tenant", userId: "ttl-user", roles: ["member"], isActive: true, sessionId: "ttl-session" })!;
  const scope = createArtifactDownloadScope(context, "ttl-message")!;
  let requests = 0;
  let fail = false;
  t.mock.method(globalThis, "fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    assert.match(String(input), /^\/api\/ai\/artifacts\/(ttl-(true|false)|capacity-64)\/download$/);
    assert.equal(init?.credentials, "include");
    requests += 1;
    return new Response("synthetic artifact", { status: fail ? 500 : 200 });
  });
  try {
    for (const initiallyFails of [false, true]) {
      fail = initiallyFails;
      const part: ArtifactPart = { type: "artifact", artifact_id: `ttl-${initiallyFails}`, artifact_type: "file", label: "report.txt", content_type: "text/plain", size_bytes: 1, download_url: `/api/ai/artifacts/ttl-${initiallyFails}/download` };
      await act(async () => root.render(createElement(StrictMode, null, createElement(MessagePartRenderer, { key: part.artifact_id, part, partIndex: 0, isLast: true, messageId: "ttl-message", artifactDownloadScope: scope }))));
      const button = dom.window.document.querySelector("button") as HTMLButtonElement;
      const before = requests;
      await act(async () => button.click());
      assert.equal(requests, before + 1);
      assert.equal(!!dom.window.document.querySelector('[role="alert"]'), initiallyFails);
      await act(async () => t.mock.timers.tick(30_001));
      assert.equal(dom.window.document.querySelector("button"), button, "the original DOM button stays mounted");
      fail = false;
      await act(async () => button.click());
      assert.equal(requests, before + 2, "the original card must start another authenticated download after TTL");
      assert.equal(dom.window.document.querySelector('[role="alert"]'), null);
    }
    // Commit 64 owners first, then add a card beyond the per-scope hard cap.
    clearArtifactDownloadScope(context);
    const cards = Array.from({ length: 65 }, (_, index) => {
      const part: ArtifactPart = { type: "artifact", artifact_id: `capacity-${index}`, artifact_type: "file", label: `capacity-${index}.txt`, content_type: "text/plain", size_bytes: 1, download_url: `/api/ai/artifacts/capacity-${index}/download` };
      return createElement(MessagePartRenderer, { key: part.artifact_id, part, partIndex: index, isLast: index === 64, messageId: "ttl-message", artifactDownloadScope: scope });
    });
    await act(async () => root.render(createElement(StrictMode, null, createElement("div", null, cards.slice(0, 64)))));
    await act(async () => root.render(createElement(StrictMode, null, createElement("div", null, cards))));
    const retry = dom.window.document.querySelectorAll("button")[64] as HTMLButtonElement;
    assert.ok(retry);
    assert.ok(dom.window.document.querySelector('[role="alert"]'));
    const beforeCapacityRetry = requests;
    await act(async () => retry.click());
    assert.equal(requests, beforeCapacityRetry, "capacity still refuses new work while owners are mounted");
    // Reuse the exact React element so releasing siblings does not reacquire the
    // retained card's controller through a render and accidentally mask the bug.
    await act(async () => root.render(createElement(StrictMode, null, createElement("div", null, cards[64]))));
    await act(async () => t.mock.timers.tick(30_001));
    assert.equal(dom.window.document.querySelector("button"), retry);
    assert.ok(dom.window.document.querySelector('[role="alert"]'), "the original refused controller remains mounted");
    await act(async () => retry.click());
    assert.equal(requests, beforeCapacityRetry + 1, "the original Retry admits into the registry after capacity is released");
    assert.equal(dom.window.document.querySelector('[role="alert"]'), null);
    const beforeRevocation = requests;
    clearArtifactDownloadScope(context);
    await act(async () => (dom.window.document.querySelector("button") as HTMLButtonElement).click());
    assert.equal(requests, beforeRevocation, "explicit route/auth scope revocation still blocks the stale card");
  } finally {
    act(() => root.unmount());
    clearArtifactDownloadScope(context);
    dom.window.close();
    globals.forEach((key, index) => {
      const descriptor = saved[index];
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else Reflect.deleteProperty(globalThis, key);
    });
  }
});
