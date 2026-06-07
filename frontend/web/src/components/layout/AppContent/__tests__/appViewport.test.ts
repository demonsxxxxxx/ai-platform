import test from "node:test";
import assert from "node:assert/strict";
import {
  getAppViewportState,
  getAppViewportHeightCssValue,
  isKeyboardViewport,
  shouldPreferVisibleViewportHeight,
  shouldUpdateAppViewportHeight,
} from "../appViewport.ts";

test("uses visual viewport height only when the keyboard has reduced the viewport", () => {
  assert.equal(
    getAppViewportHeightCssValue({
      visualViewportHeight: 512.4,
      windowInnerHeight: 800,
    }),
    "512px",
  );
});

test("lets CSS dynamic viewport units handle normal fullscreen sizing", () => {
  assert.equal(
    getAppViewportHeightCssValue({
      visualViewportHeight: 760,
      windowInnerHeight: 800,
    }),
    null,
  );
});

test("uses visible viewport height for direct mobile browser chrome", () => {
  assert.equal(
    getAppViewportHeightCssValue({
      visualViewportHeight: 724.6,
      windowInnerHeight: 800,
      preferVisibleViewportHeight: true,
    }),
    "725px",
  );
});

test("keeps standalone fullscreen sizing even when visual viewport is shorter", () => {
  assert.equal(
    getAppViewportHeightCssValue({
      visualViewportHeight: 724.6,
      windowInnerHeight: 800,
      preferVisibleViewportHeight: false,
    }),
    null,
  );
});

test("does not force a height without visual viewport data", () => {
  assert.equal(
    getAppViewportHeightCssValue({
      visualViewportHeight: null,
      windowInnerHeight: 760,
    }),
    null,
  );
});

test("does not force a height when no measured height is available", () => {
  assert.equal(
    getAppViewportHeightCssValue({
      visualViewportHeight: null,
      windowInnerHeight: null,
    }),
    null,
  );
});

test("detects keyboard viewport only after a significant visual viewport reduction", () => {
  assert.equal(
    isKeyboardViewport({
      visualViewportHeight: 690,
      windowInnerHeight: 800,
    }),
    true,
  );
  assert.equal(
    isKeyboardViewport({
      visualViewportHeight: 720,
      windowInnerHeight: 800,
    }),
    false,
  );
});

test("ignores tiny visual viewport height jitter", () => {
  assert.equal(shouldUpdateAppViewportHeight("512px", "512px"), false);
  assert.equal(shouldUpdateAppViewportHeight("512px", "513px"), false);
  assert.equal(shouldUpdateAppViewportHeight("512px", "516px"), true);
});

test("tracks keyboard viewport height, top offset, and covered bottom area", () => {
  assert.deepEqual(
    getAppViewportState({
      visualViewportHeight: 512.4,
      visualViewportOffsetTop: 36.2,
      windowInnerHeight: 800,
      editableFocused: true,
    }),
    {
      heightCssValue: "512px",
      offsetTopCssValue: "36px",
      keyboardInsetCssValue: "252px",
      keyboardOpen: true,
    },
  );
});

test("does not force keyboard viewport variables when no editable field is focused", () => {
  assert.deepEqual(
    getAppViewportState({
      visualViewportHeight: 512.4,
      visualViewportOffsetTop: 36.2,
      windowInnerHeight: 800,
      editableFocused: false,
      preferVisibleViewportHeight: true,
    }),
    {
      heightCssValue: "512px",
      offsetTopCssValue: null,
      keyboardInsetCssValue: null,
      keyboardOpen: false,
    },
  );
});

test("does not use visible viewport preference when visual viewport is taller", () => {
  assert.deepEqual(
    getAppViewportState({
      visualViewportHeight: 820,
      visualViewportOffsetTop: 0,
      windowInnerHeight: 800,
      editableFocused: false,
      preferVisibleViewportHeight: true,
    }),
    {
      heightCssValue: null,
      offsetTopCssValue: null,
      keyboardInsetCssValue: null,
      keyboardOpen: false,
    },
  );
});

test("prefers visible viewport height only for direct mobile browser access", () => {
  assert.equal(
    shouldPreferVisibleViewportHeight({
      isMobileDevice: true,
      isStandaloneDisplayMode: false,
      hasVisualViewport: true,
    }),
    true,
  );
  assert.equal(
    shouldPreferVisibleViewportHeight({
      isMobileDevice: true,
      isStandaloneDisplayMode: true,
      hasVisualViewport: true,
    }),
    false,
  );
  assert.equal(
    shouldPreferVisibleViewportHeight({
      isMobileDevice: false,
      isStandaloneDisplayMode: false,
      hasVisualViewport: true,
    }),
    false,
  );
  assert.equal(
    shouldPreferVisibleViewportHeight({
      isMobileDevice: true,
      isStandaloneDisplayMode: false,
      hasVisualViewport: false,
    }),
    false,
  );
});
