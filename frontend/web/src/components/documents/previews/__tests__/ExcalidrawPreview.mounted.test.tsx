import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test, { after } from "node:test";
import { act, createElement } from "react";
import { createLobeTestRoot } from "../../../common/__tests__/lobeUiTestRoot";
import { installSelectTestDom } from "../../../common/__tests__/selectTestDom";

// Only the expensive SVG export boundary is controlled. The actual preview,
// toolbar, state handlers and application providers stay mounted throughout.
const exportKey = "__excalidrawPreviewTestExport";
const exportDescriptor = Object.getOwnPropertyDescriptor(globalThis, exportKey);
const hooks = registerHooks({
  resolve(specifier, context, nextResolve) {
    return specifier === "@excalidraw/excalidraw"
      ? { url: "excalidraw-preview-test:exporter", shortCircuit: true }
      : nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    return url === "excalidraw-preview-test:exporter"
      ? {
        format: "module", shortCircuit: true,
        source: `export const exportToSvg = options => globalThis.${exportKey}(options);`,
      }
      : nextLoad(url, context);
  },
});
const environment = installSelectTestDom();
const serializerDescriptor = Object.getOwnPropertyDescriptor(globalThis, "XMLSerializer");
Object.defineProperty(globalThis, "XMLSerializer", {
  configurable: true, writable: true, value: window.XMLSerializer,
});
const { default: ExcalidrawPreview } = await import("../ExcalidrawPreview.tsx");
const { default: i18n } = await import("../../../../i18n");

after(() => {
  hooks.deregister();
  if (exportDescriptor) Object.defineProperty(globalThis, exportKey, exportDescriptor);
  else Reflect.deleteProperty(globalThis, exportKey);
  if (serializerDescriptor) Object.defineProperty(globalThis, "XMLSerializer", serializerDescriptor);
  else Reflect.deleteProperty(globalThis, "XMLSerializer");
  environment.close();
});

interface Geometry {
  containerWidth: number;
  containerHeight: number;
  svgWidth: number;
  svgHeight: number;
}
const normalGeometry: Geometry = {
  containerWidth: 500, containerHeight: 300, svgWidth: 1000, svgHeight: 600,
};

async function mountPreview(geometry: Geometry = normalGeometry) {
  const host = document.createElement("div");
  const root = await createLobeTestRoot(host);
  let exported!: () => void;
  const exportCalled = new Promise<void>(resolve => { exported = resolve; });
  Object.defineProperty(globalThis, exportKey, {
    configurable: true,
    value: async () => {
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("data-preview-fixture", "diagram");
      svg.setAttribute("width", String(geometry.svgWidth));
      svg.setAttribute("height", String(geometry.svgHeight));
      svg.setAttribute("viewBox", `0 0 ${geometry.svgWidth} ${geometry.svgHeight}`);
      exported();
      return svg;
    },
  });
  await act(async () => {
    root.render(createElement(ExcalidrawPreview, { data: JSON.stringify({ elements: [{ id: "fixture" }] }) }));
  });
  await act(async () => { await exportCalled; });
  const svg = host.querySelector<SVGSVGElement>('svg[data-preview-fixture="diagram"]');
  assert.ok(svg, "real preview commits the exported SVG");
  const transform = svg.parentElement;
  const viewport = transform?.parentElement;
  assert.ok(transform);
  assert.ok(viewport);
  const scale = () => {
    const match = /scale\(([^)]+)\)/.exec(transform.style.transform);
    assert.ok(match, "preview owns an explicit scale transform");
    return Number(match[1]);
  };
  Object.defineProperties(viewport, {
    clientWidth: { configurable: true, get: () => geometry.containerWidth },
    clientHeight: { configurable: true, get: () => geometry.containerHeight },
  });
  // React can replace serialized SVG children on a state update. Prototype
  // geometry follows the current fixture node rather than a detached snapshot.
  const svgPrototype = window.SVGElement.prototype;
  const descriptors = new Map<string, PropertyDescriptor | undefined>();
  const installGeometry = (key: string, descriptor: PropertyDescriptor) => {
    descriptors.set(key, Object.getOwnPropertyDescriptor(svgPrototype, key));
    Object.defineProperty(svgPrototype, key, { configurable: true, ...descriptor });
  };
  const isFixture = (node: Element) => node.getAttribute("data-preview-fixture") === "diagram";
  const nativeBounds = svgPrototype.getBoundingClientRect;
  let interpolatedScale: number | null = null;
  // Browser bounds include the *rendered* transform; client dimensions do not.
  installGeometry("getBoundingClientRect", {
    value(this: SVGElement) {
      if (!isFixture(this)) return nativeBounds.call(this);
      const currentScale = interpolatedScale ?? scale();
      return new DOMRect(0, 0, geometry.svgWidth * currentScale, geometry.svgHeight * currentScale);
    },
  });
  for (const [dimension, intrinsic] of [["clientWidth", "svgWidth"], ["clientHeight", "svgHeight"]] as const) {
    const nativeGetter = Object.getOwnPropertyDescriptor(window.Element.prototype, dimension)?.get;
    installGeometry(dimension, {
      get(this: SVGElement) { return isFixture(this) ? geometry[intrinsic] : nativeGetter?.call(this) ?? 0; },
    });
  }
  return {
    host, viewport, transform, scale,
    get svg() {
      const current = host.querySelector<SVGSVGElement>('svg[data-preview-fixture="diagram"]');
      assert.ok(current);
      return current;
    },
    setInterpolatedScale(value: number) { interpolatedScale = value; },
    async click(key: string) {
      const button = [...host.querySelectorAll<HTMLButtonElement>("button")]
        .find(node => node.title === i18n.t(key));
      assert.ok(button, `Missing real toolbar button: ${key}`);
      await act(async () => { button.click(); });
    },
    async close() {
      await act(async () => { root.unmount(); });
      for (const [key, descriptor] of descriptors) {
        if (descriptor) Object.defineProperty(svgPrototype, key, descriptor);
        else Reflect.deleteProperty(svgPrototype, key);
      }
    },
  };
}

test("mounted Excalidraw repeated Fit stays at 50% with transformed SVG bounds", async () => {
  const view = await mountPreview();
  try {
    assert.equal(view.scale(), 1);
    assert.equal(view.svg.getBoundingClientRect().width, 1000);
    for (let attempt = 0; attempt < 4; attempt++) {
      await view.click("documents.fitToScreen");
      assert.equal(view.scale(), 0.5, `Fit ${attempt + 1} must use intrinsic diagram size`);
      assert.equal(view.svg.getBoundingClientRect().width, 500);
      assert.match(view.host.textContent ?? "", /50%/);
    }
  } finally { await view.close(); }
});

test("mounted Excalidraw Fit after zoom and pan returns to the same centered scale", async () => {
  const view = await mountPreview();
  try {
    await view.click("documents.fitToScreen");
    await view.click("documents.zoomIn");
    assert.equal(view.scale(), 0.625);
    assert.equal(view.svg.getBoundingClientRect().width, 625);
    await act(async () => {
      view.viewport.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, button: 0, clientX: 20, clientY: 30 }));
    });
    await act(async () => {
      view.viewport.dispatchEvent(new MouseEvent("mousemove", { bubbles: true, clientX: 80, clientY: 110 }));
    });
    await act(async () => {
      view.viewport.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    });
    assert.match(view.transform.style.transform, /translate\(60px, 80px\)/);
    await view.click("documents.fitToScreen");
    assert.equal(view.scale(), 0.5, "manual zoom must not influence absolute fit scale");
    assert.match(view.transform.style.transform, /translate\(0px, 0px\)/);
    await view.click("documents.zoomOut");
    assert.equal(view.scale(), 0.4);
    await view.click("documents.fitToScreen");
    assert.equal(view.scale(), 0.5);
  } finally { await view.close(); }
});

test("mounted Excalidraw Fit stays stable during a zoom transition", async () => {
  const view = await mountPreview();
  try {
    await view.click("documents.fitToScreen");
    await view.click("documents.zoomIn");
    assert.equal(view.scale(), 0.625);
    // During the real 0.2s CSS transition, DOMRect reflects an intermediate
    // rendered transform, not the scale currently committed in React state.
    view.setInterpolatedScale(0.58);
    assert.equal(view.svg.getBoundingClientRect().width, 580);
    assert.equal(view.svg.clientWidth, 1000);
    await view.click("documents.fitToScreen");
    assert.equal(view.scale(), 0.5);
    view.setInterpolatedScale(0.53);
    await view.click("documents.fitToScreen");
    assert.equal(view.scale(), 0.5, "rapid repeated Fit must ignore transition progress");
  } finally { await view.close(); }
});

for (const zeroDimension of ["containerWidth", "containerHeight", "svgWidth", "svgHeight"] as const) {
  test(`mounted Excalidraw Fit safely ignores zero ${zeroDimension}`, async () => {
    const view = await mountPreview({ ...normalGeometry, [zeroDimension]: 0 });
    try {
      await view.click("documents.zoomIn");
      const before = view.scale();
      for (let attempt = 0; attempt < 2; attempt++) {
        await view.click("documents.fitToScreen");
        assert.equal(view.scale(), before, "unmeasurable geometry must preserve the existing view");
        assert.ok(Number.isFinite(view.scale()) && view.scale() > 0);
        assert.doesNotMatch(view.transform.style.transform, /NaN|Infinity/);
      }
    } finally { await view.close(); }
  });
}
