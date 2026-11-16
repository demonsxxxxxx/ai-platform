/**
 * The one place that connects base.css tokens to the Lobe UI theme.
 *
 * base.css owns the values (static, first paint). Lobe components compute their
 * colors from the theme passed to LobeUiProvider instead of reading CSS
 * variables, so the pairs below are checked against Lobe's real algorithm in
 * styles/__tests__/themeContract.test.ts. Changing either side alone fails
 * that check. Rules: docs/frontend/visual-guidelines.md section 3.
 */

/** Token overrides passed to Lobe ThemeProvider; values mirror base.css. */
export const LOBE_THEME_TOKEN_OVERRIDES = {
  borderRadius: 8,
  fontFamily: "var(--font-ui)",
} as const;

/** base.css variables whose value must equal the Lobe design token. */
export const LOBE_COLOR_TOKEN_PAIRS = [
  ["--theme-primary", "colorPrimary"],
  ["--theme-primary-hover", "colorPrimaryHover"],
  ["--theme-bg-card", "colorBgContainer"],
  ["--theme-bg-sidebar", "colorBgLayout"],
  ["--theme-border", "colorBorder"],
  ["--theme-text", "colorText"],
  ["--theme-text-secondary", "colorTextSecondary"],
  ["--theme-text-tertiary", "colorTextTertiary"],
  ["--theme-text-quaternary", "colorTextQuaternary"],
  ["--theme-success", "colorSuccess"],
  ["--theme-info", "colorInfo"],
  ["--theme-warning", "colorWarning"],
  ["--theme-danger", "colorError"],
] as const;

/** base.css geometry variables whose pixel value must equal a Lobe token. */
export const LOBE_GEOMETRY_TOKEN_PAIRS = [["--radius-control", "borderRadius"]] as const;
