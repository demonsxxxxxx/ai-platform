import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { SidebarSkeleton } from "../../components/skeletons/SidebarSkeleton";
import { createLobeTestRoot } from "../../components/common/__tests__/lobeUiTestRoot";
import { installSelectTestDom } from "../../components/common/__tests__/selectTestDom";
import { SIDEBAR_COLLAPSED_STORAGE_KEY, useSidebarPreference } from "../../hooks/useSidebarPreference";

const cases = [
  ["missing key", null, false],
  ["true", "true", true],
  ["false", "false", false],
  ["invalid", "garbage", false],
  ["getItem throws", null, false],
  ["storage getter throws", null, false],
  ["storage absent", null, false],
] as const;

for (const [label, stored, expected] of cases) {
  test(`skeleton and mounted preference agree: ${label}`, async () => {
    const dom = installSelectTestDom();
    const container = document.createElement("div");
    const root = await createLobeTestRoot(container);
    const descriptor = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
    const originalFetch = globalThis.fetch;
    let reads = 0;
    let writes = 0;
    let requests = 0;
    const storage = {
      getItem(key: string) {
        if (key !== SIDEBAR_COLLAPSED_STORAGE_KEY) return null;
        reads++;
        if (label === "getItem throws") throw new Error("blocked read");
        return stored;
      },
      setItem() { writes++; },
    };
    Object.defineProperty(globalThis, "localStorage", { configurable: true, value: storage });
    if (label === "storage getter throws") Object.defineProperty(globalThis, "localStorage", {
      configurable: true, get() { throw new Error("blocked getter"); },
    });
    if (label === "storage absent") Reflect.deleteProperty(globalThis, "localStorage");
    globalThis.fetch = async () => { requests++; throw new Error("unexpected request"); };
    function Probe() {
      const [collapsed] = useSidebarPreference();
      return <><output>{String(collapsed)}</output><section><SidebarSkeleton /></section></>;
    }
    try {
      await React.act(async () => root.render(<Probe />));
      assert.equal(container.querySelector("output")?.textContent, String(expected));
      const skeleton = container.querySelector("section > div") as HTMLElement;
      assert.ok(skeleton);
      assert.equal(skeleton.style.width === "var(--sidebar-rail-width)", expected);
      assert.equal(skeleton.classList.contains("w-64"), !expected);
      if (label !== "storage getter throws" && label !== "storage absent") assert.equal(reads, 2);
      assert.equal(writes, 0, "initial preference and skeleton never persist");
      assert.equal(requests, 0, "loading shell does not request business data");
    } finally {
      await React.act(async () => root.unmount());
      globalThis.fetch = originalFetch;
      if (descriptor) Object.defineProperty(globalThis, "localStorage", descriptor);
      else Reflect.deleteProperty(globalThis, "localStorage");
      dom.close();
    }
  });
}

test("no-window SSR keeps the hook and skeleton branch expanded", () => {
  const descriptors = new Map(["window", "localStorage"].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  for (const key of descriptors.keys()) Reflect.deleteProperty(globalThis, key);
  try {
    assert.equal(typeof window, "undefined");
    assert.equal(typeof localStorage, "undefined");
    function Probe() { return <output>{String(useSidebarPreference()[0])}</output>; }
    assert.equal(renderToStaticMarkup(<Probe />), "<output>false</output>");
    // Execute the actual component's choice without rendering Lobe leaves in SSR.
    assert.equal(SidebarSkeleton().type.name, "SidebarExpandedSkeleton");
  } finally {
    for (const [key, descriptor] of descriptors) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else Reflect.deleteProperty(globalThis, key);
    }
  }
});
