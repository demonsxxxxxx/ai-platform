import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test, { after } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
// @ts-expect-error jsdom is the pinned mounted-test runtime.
import { JSDOM } from "jsdom";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

// Control only conversion boundaries. WordPreview's effect cleanup, renderText,
// catch/finally and React state are the real implementation under test.
const hooks = registerHooks({
  load(url, context, nextLoad) {
    let source: string | undefined;
    if (/\/legacyDocPreviewUtils\.ts(?:\?|$)/.test(url)) source = `
      export const isLegacyDocArrayBuffer = bytes => new Uint8Array(bytes)[0] === 1;
      export const extractLegacyDocText = () => globalThis.__wordCancellationTest.legacy();
    `;
    if (/\/wordPreviewUtils\.ts(?:\?|$)/.test(url)) source = `
      export const docxTextToHtml = text => '<p>' + text + '</p>';
      export const isDocxSafeForMammoth = async () => false;
      export const extractDocxTextFallback = () => globalThis.__wordCancellationTest.fallback();
    `;
    if (/\/wordPreviewRenderer\.ts(?:\?|$)/.test(url)) source = `
      export const measureDocxPreview = () => ({ width: 900, height: 1400 });
      export const renderDocxPreviewHtml = async ({ arrayBuffer, container }) => {
        if (new Uint8Array(arrayBuffer)[0] === 2) throw new Error('synthetic DOCX conversion failure');
        container.innerHTML = '<section class="docx">Current document B</section>';
        globalThis.__wordCancellationTest.renderedCurrent();
        return { kind: 'docx-preview' };
      };
    `;
    return source === undefined ? nextLoad(url, context) : { format: "module", source, shortCircuit: true };
  },
});
after(() => hooks.deregister());
const t = (key: string) => key;

type Scenario = "legacy" | "docx";
async function runScenario(scenario: Scenario, cancelled: boolean) {
  const dom = new JSDOM("<!doctype html><html><body><div id='root'></div></body></html>");
  const saved = new Map<string, PropertyDescriptor | undefined>();
  const install = (key: string, value: unknown) => {
    saved.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
    Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
  };
  for (const key of ["window", "document", "DOMParser", "Node", "Element", "HTMLElement", "MutationObserver"]) install(key, dom.window[key]);
  install("IS_REACT_ACT_ENVIRONMENT", true);
  install("ResizeObserver", class { observe() {} disconnect() {} });
  const completion = deferred<string>();
  const entered = deferred<void>();
  const renderedCurrent = deferred<void>();
  const waitForCompletion = () => { entered.resolve(); return completion.promise; };
  install("__wordCancellationTest", {
    legacy: waitForCompletion,
    fallback: waitForCompletion,
    renderedCurrent: () => renderedCurrent.resolve(),
  });
  const host = document.getElementById("root")!;
  const root = createRoot(host);
  const { default: WordPreview } = await import("../WordPreview.tsx");
  const oldError = console.error;
  const errors: unknown[][] = [];
  console.error = (...args) => { errors.push(args); };
  try {
    const bytesA = Uint8Array.of(scenario === "legacy" ? 1 : 2).buffer;
    await act(async () => {
      root.render(<WordPreview arrayBuffer={bytesA} t={t} />);
    });
    // This promise observes the actual extraction call, with no wall-clock race.
    await act(async () => { await entered.promise; });
    if (cancelled) {
      await act(async () => {
        root.render(<WordPreview arrayBuffer={Uint8Array.of(3).buffer} t={t} />);
      });
      // Dynamic module loading can finish after act on Windows. Observe the
      // actual current renderer before asserting or completing the old request.
      await act(async () => { await renderedCurrent.promise; });
      assert.match(host.textContent!, /Current document B/);
    }
    await act(async () => {
      if (scenario === "legacy") {
        // A late nonempty legacy result makes renderText return false only
        // because the old effect was cancelled. It must not become an error.
        completion.resolve(cancelled ? "Obsolete document A" : "");
      } else {
        completion.reject(new Error("synthetic DOCX fallback failure"));
      }
      await Promise.resolve();
    });
    if (cancelled) {
      assert.match(host.textContent!, /Current document B/);
      assert.doesNotMatch(host.textContent!, /documents.wordConversionError|Obsolete document A/);
      assert.ok(host.querySelector("section.docx"));
      assert.ok(host.querySelector("button[aria-label='imageViewer.zoomIn']"));
    } else {
      assert.match(host.textContent!, /documents.wordConversionError/);
      assert.equal(host.querySelector("section.docx"), null);
    }
    if (scenario === "docx") {
      assert.ok(errors.some(args => String(args[0]).includes("Failed to extract DOCX text fallback")));
    }
  } finally {
    console.error = oldError;
    await act(async () => root.unmount());
    dom.window.close();
    for (const [key, descriptor] of saved) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else Reflect.deleteProperty(globalThis, key);
    }
  }
}

test("cancelled legacy extraction cannot replace the newer document with an error", { timeout: 5000 }, () => runScenario("legacy", true));
test("cancelled DOCX fallback failure cannot replace the newer document with an error", { timeout: 5000 }, () => runScenario("docx", true));
test("current legacy extraction with no readable text still displays its error", { timeout: 5000 }, () => runScenario("legacy", false));
test("current DOCX fallback failure still displays its error", { timeout: 5000 }, () => runScenario("docx", false));
