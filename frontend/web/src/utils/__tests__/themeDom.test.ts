import test from "node:test";
import assert from "node:assert/strict";

import {
  LEGACY_THEME_STORAGE_KEY,
  THEME_STORAGE_KEY,
  applyThemeToDocument,
  getInitialThemePreference,
} from "../themeDom.ts";

test("getInitialThemePreference prefers persisted theme over system preference", () => {
  const env = {
    localStorage: {
      getItem: (key: string) => (key === THEME_STORAGE_KEY ? "light" : null),
    },
    matchMedia: () => ({ matches: true }),
  };

  assert.equal(getInitialThemePreference(env), "light");
});

test("getInitialThemePreference can migrate legacy theme preference", () => {
  const env = {
    localStorage: {
      getItem: (key: string) =>
        key === LEGACY_THEME_STORAGE_KEY ? "dark" : null,
    },
    matchMedia: () => ({ matches: false }),
  };

  assert.equal(getInitialThemePreference(env), "dark");
});

test("getInitialThemePreference falls back to dark system preference", () => {
  const env = {
    localStorage: {
      getItem: () => null,
    },
    matchMedia: () => ({ matches: true }),
  };

  assert.equal(getInitialThemePreference(env), "dark");
});

test("applyThemeToDocument synchronously toggles dark class and browser chrome", () => {
  const classes = new Set<string>(["dark"]);
  const metaValues = new Map<string, string>();
  const themeColorElements = [
    {
      setAttribute: (_name: string, value: string) => {
        metaValues.set('meta[name="theme-color"]:default', value);
      },
    },
  ];
  const documentLike = {
    documentElement: {
      classList: {
        add: (name: string) => classes.add(name),
        remove: (name: string) => classes.delete(name),
      },
    },
    querySelector: (selector: string) =>
      selector === 'meta[name="theme-color"]' ||
      selector === 'meta[name="apple-mobile-web-app-status-bar-style"]'
        ? {
            setAttribute: (_name: string, value: string) => {
              metaValues.set(selector, value);
            },
          }
        : null,
    querySelectorAll: (selector: string) =>
      selector === 'meta[name="theme-color"]' ? themeColorElements : [],
  };

  applyThemeToDocument("light", documentLike);

  assert.equal(classes.has("dark"), false);
  assert.equal(metaValues.get('meta[name="theme-color"]:default'), "#f5f5f4");
  assert.equal(
    metaValues.get('meta[name="apple-mobile-web-app-status-bar-style"]'),
    "default",
  );
});

test("applyThemeToDocument updates every theme-color meta tag", () => {
  const metaValues: string[] = [];
  const documentLike = {
    documentElement: {
      classList: {
        add: () => {},
        remove: () => {},
      },
    },
    querySelector: (selector: string) =>
      selector === 'meta[name="apple-mobile-web-app-status-bar-style"]'
        ? {
            setAttribute: () => {},
          }
        : null,
    querySelectorAll: (selector: string) =>
      selector === 'meta[name="theme-color"]'
        ? [0, 1, 2].map((index) => ({
            setAttribute: (_name: string, value: string) => {
              metaValues[index] = value;
            },
          }))
        : [],
  };

  applyThemeToDocument("dark", documentLike);

  assert.deepEqual(metaValues, ["#151210", "#151210", "#151210"]);
});

test("applyThemeToDocument keeps the page background in sync for system bars", () => {
  const rootStyle = new Map<string, string>();
  const bodyStyle = new Map<string, string>();
  const documentLike = {
    documentElement: {
      classList: {
        add: () => {},
        remove: () => {},
      },
      style: {
        setProperty: (name: string, value: string) => {
          rootStyle.set(name, value);
        },
      },
    },
    body: {
      style: {
        setProperty: (name: string, value: string) => {
          bodyStyle.set(name, value);
        },
      },
    },
    querySelector: () => null,
    querySelectorAll: () => [],
  };

  applyThemeToDocument("dark", documentLike);

  assert.equal(rootStyle.get("background-color"), "#151210");
  assert.equal(rootStyle.get("color-scheme"), "dark");
  assert.equal(bodyStyle.get("background-color"), "#151210");
  assert.equal(bodyStyle.get("color-scheme"), "dark");
});
