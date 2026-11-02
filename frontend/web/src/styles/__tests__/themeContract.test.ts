import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { theme, type ThemeConfig } from "antd";

import {
  LOBE_COLOR_TOKEN_PAIRS,
  LOBE_GEOMETRY_TOKEN_PAIRS,
  LOBE_THEME_TOKEN_OVERRIDES,
} from "../themeContract.ts";

// The package exports this module under "./es/*" but ships no declaration for it.
const lobeThemeModule: string = "@lobehub/ui/es/styles/theme/antdTheme";
const { createLobeAntdTheme } = (await import(lobeThemeModule)) as {
  createLobeAntdTheme: (options: { appearance: "light" | "dark" }) => ThemeConfig;
};

const baseCss = readFileSync(new URL("../base.css", import.meta.url), "utf8");

/** Collect `--name: value` declarations from the first top-level block for a selector. */
function readBlock(selector: string): Map<string, string> {
  const start = baseCss.search(new RegExp(`^${selector.replace(".", "\\.")} \\{`, "m"));
  assert.notEqual(start, -1, `base.css has no top-level ${selector} block`);
  const body = baseCss.slice(start, baseCss.indexOf("\n}", start));
  const values = new Map<string, string>();
  for (const match of body.matchAll(/^\s*(--[\w-]+):\s*([^;]+);/gm)) values.set(match[1], match[2].trim());
  return values;
}

function lobeTokens(appearance: "light" | "dark"): Record<string, unknown> {
  const config = createLobeAntdTheme({ appearance });
  return theme.getDesignToken({
    ...config,
    token: { ...config.token, ...LOBE_THEME_TOKEN_OVERRIDES },
  }) as unknown as Record<string, unknown>;
}

const lightCss = readBlock(":root");
const darkCss = new Map([...lightCss, ...readBlock(".dark")]);

for (const [appearance, css] of [["light", lightCss], ["dark", darkCss]] as const) {
  test(`base.css ${appearance} colors equal the Lobe theme output`, () => {
    const tokens = lobeTokens(appearance);
    for (const [cssVar, token] of LOBE_COLOR_TOKEN_PAIRS) {
      assert.equal(
        css.get(cssVar)?.toLowerCase(),
        String(tokens[token]).toLowerCase(),
        `${cssVar} must equal Lobe ${token} (${appearance}); change base.css and themeContract.ts together`,
      );
    }
  });
}

// Lobe's color algorithm does not derive geometry or font; the provider's
// explicit overrides are what Lobe receives, so compare against those.
test("base.css geometry and font equal the Lobe token overrides", () => {
  for (const [cssVar, token] of LOBE_GEOMETRY_TOKEN_PAIRS) {
    assert.equal(lightCss.get(cssVar), `${LOBE_THEME_TOKEN_OVERRIDES[token]}px`, `${cssVar} must equal Lobe ${token}`);
  }
  assert.equal(LOBE_THEME_TOKEN_OVERRIDES.fontFamily, "var(--font-ui)");
  assert.ok(lightCss.get("--font-ui"), "base.css defines --font-ui");
  const provider = readFileSync(new URL("../../contexts/LobeUiProvider.tsx", import.meta.url), "utf8");
  assert.match(provider, /theme=\{\{ token: LOBE_THEME_TOKEN_OVERRIDES \}\}/);
});

test("chat geometry has one definition", () => {
  assert.equal(lightCss.get("--chat-column-max"), "48rem");
  assert.equal(lightCss.get("--radius-composer"), "28px");
  assert.equal(lightCss.get("--radius-user-bubble"), "22px");
  const chatCss = readFileSync(new URL("../chat.css", import.meta.url), "utf8");
  const composerRule = chatCss.match(/\.chat-input-container \{[^}]*\}/)?.[0] ?? "";
  assert.match(composerRule, /border-radius:\s*var\(--radius-composer\)/);
  assert.doesNotMatch(composerRule, /min-height/);
});

test("Tailwind font-sans resolves to the shared UI font", async () => {
  const tailwindConfig: string = new URL("../../../tailwind.config.js", import.meta.url).href;
  const { default: config } = (await import(tailwindConfig)) as {
    default: { theme: { extend: { fontFamily: { sans: string[] } } } };
  };
  assert.deepEqual(config.theme.extend.fontFamily.sans, ["var(--font-ui)"]);
});
