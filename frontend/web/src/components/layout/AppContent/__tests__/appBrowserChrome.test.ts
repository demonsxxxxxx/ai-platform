import test from "node:test";
import assert from "node:assert/strict";
import {
  getBrowserChromeNudgeScrollY,
  shouldNudgeBrowserChrome,
} from "../appBrowserChrome.ts";

test("nudges browser chrome only for direct mobile browser access", () => {
  assert.equal(
    shouldNudgeBrowserChrome({
      isMobileDevice: true,
      isStandaloneDisplayMode: false,
      hasVisualViewport: true,
    }),
    true,
  );
  assert.equal(
    shouldNudgeBrowserChrome({
      isMobileDevice: true,
      isStandaloneDisplayMode: true,
      hasVisualViewport: true,
    }),
    false,
  );
  assert.equal(
    shouldNudgeBrowserChrome({
      isMobileDevice: false,
      isStandaloneDisplayMode: false,
      hasVisualViewport: true,
    }),
    false,
  );
  assert.equal(
    shouldNudgeBrowserChrome({
      isMobileDevice: true,
      isStandaloneDisplayMode: false,
      hasVisualViewport: false,
    }),
    false,
  );
});

test("scrolls one pixel only when the page has a scroll runway", () => {
  assert.equal(
    getBrowserChromeNudgeScrollY({
      scrollHeight: 801,
      innerHeight: 800,
    }),
    1,
  );
  assert.equal(
    getBrowserChromeNudgeScrollY({
      scrollHeight: 800,
      innerHeight: 800,
    }),
    0,
  );
});
