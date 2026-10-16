import { register } from "node:module";

register("./lobe-ui-test-loader.mjs", import.meta.url);

// JSDOM supplies neither layout nor OS media preferences. Individual DOM
// fixtures can provide their own media listener implementation and values.
if (!globalThis.matchMedia) {
  globalThis.matchMedia = (media) => globalThis.window?.matchMedia?.(media) ?? {
    matches: false, media, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {},
    dispatchEvent() { return true; },
  };
}
