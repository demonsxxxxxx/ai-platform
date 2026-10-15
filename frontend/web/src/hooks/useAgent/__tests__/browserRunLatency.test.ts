import assert from "node:assert/strict";
import test from "node:test";
// @ts-expect-error jsdom is the pinned mounted-test runtime.
import { JSDOM } from "jsdom";
import { sessionApi } from "../../../services/api/session";
import {
  beginBrowserRunLatency, bindBrowserRunLatency, retireBrowserRunLatency,
  watchBrowserRunText,
} from "../browserRunLatency";

for (const scenario of ["visible", "streamed", "hidden", "history", "stale", "cancelled", "wrong-session", "duplicate", "recovery", "tool-only", "leading-empty", "stale-after-commit", "remount"] as const) {
  test(`browser first text: ${scenario}`, async () => {
    const dom = new JSDOM("<!doctype html><body><div data-browser-run-id='run-a' data-browser-session-id='session-a'><div id='text'></div></div></body>", { pretendToBeVisual: true });
    const values = { document: dom.window.document, MutationObserver: dom.window.MutationObserver };
    const previous = new Map(Object.keys(values).map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
    for (const [key, value] of Object.entries(values)) Object.defineProperty(globalThis, key, { configurable: true, value });
    const originalReport = sessionApi.reportBrowserRunLatency;
    const calls: unknown[][] = [];
    sessionApi.reportBrowserRunLatency = async (...args) => { calls.push(args); return { recorded: true }; };
    const node = dom.window.document.getElementById("text") as HTMLElement;
    Object.defineProperty(node, "getClientRects", { value: () => [{ width: 50, height: 20 }] });
    let owns = true;
    let visible = scenario !== "hidden";
    Object.defineProperty(dom.window.document, "visibilityState", { get: () => visible ? "visible" : "hidden" });
    const id = `submission-${scenario}`;
    let dispose: (() => void) | undefined;
    try {
      if (scenario !== "history") beginBrowserRunLatency(id, () => owns);
      if (scenario === "recovery") {
        owns = false;
        bindBrowserRunLatency(id, "session-a", "run-a", () => true);
      } else bindBrowserRunLatency(id, scenario === "wrong-session" ? "session-other" : "session-a", "run-a");
      if (scenario === "cancelled") retireBrowserRunLatency(id);
      if (scenario === "stale") owns = false;
      node.innerHTML = scenario === "streamed" || scenario === "remount" ? "<p></p>" : scenario === "tool-only" ? "<button>复制</button>" : scenario === "leading-empty" ? "<p><img></p><p>正文</p>" : "<p>PRIVATE_PUBLIC_TEXT_MUST_NOT_BE_UPLOADED</p>";
      dispose = watchBrowserRunText(node);
      if (scenario === "stale-after-commit") owns = false;
      if (scenario === "remount") {
        dispose?.();
        node.remove();
        dom.window.document.querySelector("[data-browser-run-id]")!.append(node);
        node.innerHTML = "<p>重新挂载的正文</p>";
        dispose = watchBrowserRunText(node);
      }
      if (scenario === "duplicate") watchBrowserRunText(node);
      await Promise.resolve();
      if (scenario === "hidden" || scenario === "streamed") {
        assert.equal(calls.length, 0);
        visible = true;
        if (scenario === "streamed") node.querySelector("p")!.textContent = "正文";
        dom.window.document.dispatchEvent(new dom.window.Event("visibilitychange"));
      }
      await Promise.resolve();
      const shouldReport = ["visible", "streamed", "hidden", "duplicate", "recovery", "leading-empty", "remount"].includes(scenario);
      assert.equal(calls.length, shouldReport ? 1 : 0);
      if (shouldReport) {
        assert.equal(calls[0][0], id);
        assert.equal(calls[0][1], "run-a");
        assert.ok(Number.isSafeInteger(calls[0][2]) && Number(calls[0][2]) >= 0);
        assert.equal(calls[0].length, 3);
        assert.doesNotMatch(JSON.stringify(calls), /PRIVATE_PUBLIC_TEXT/);
      }
    } finally {
      dispose?.();
      retireBrowserRunLatency(id);
      sessionApi.reportBrowserRunLatency = originalReport;
      dom.window.close();
      for (const [key, descriptor] of previous) {
        if (descriptor) Object.defineProperty(globalThis, key, descriptor);
        else delete (globalThis as Record<string, unknown>)[key];
      }
    }
  });
}
