import assert from "node:assert/strict";
import { installSelectTestDom } from "../../../components/common/__tests__/selectTestDom";

class TestLockManager {
  private readonly tails = new Map<string, Promise<void>>();

  async request<T>(
    name: string,
    options: { mode: "exclusive" },
    callback: () => Promise<T>,
  ): Promise<T> {
    assert.equal(options.mode, "exclusive");
    const previous = this.tails.get(name) ?? Promise.resolve();
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const tail = previous.then(() => gate);
    this.tails.set(name, tail);
    await previous;
    try {
      return await callback();
    } finally {
      release();
      if (this.tails.get(name) === tail) {
        this.tails.delete(name);
      }
    }
  }
}

export function installTestDom() {
  const dom = installSelectTestDom();
  const windowTarget = window;
  windowTarget.fetch = globalThis.fetch;
  // Submission ownership tests simulate tab-specific storage views explicitly.
  Object.defineProperty(windowTarget, "localStorage", { configurable: true, writable: true, value: windowTarget.localStorage });
  Object.assign(globalThis, { localStorage: windowTarget.localStorage, sessionStorage: windowTarget.sessionStorage });
  Object.defineProperty(navigator, "locks", { configurable: true, value: new TestLockManager() });
  return { ...dom, window: windowTarget };
}
