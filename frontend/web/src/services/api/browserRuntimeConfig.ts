import { API_BASE } from "./config";
import { authFetch } from "./fetch";

export const LAUNCHPAD_RUNTIME_URL_KEYS = [
  "lingxi",
  "sop_assistant",
  "word_translate",
  "word_review",
] as const;

export type LaunchpadRuntimeUrlKey =
  (typeof LAUNCHPAD_RUNTIME_URL_KEYS)[number];

export type LaunchpadRuntimeUrls = Readonly<
  Record<LaunchpadRuntimeUrlKey, string | null>
>;

export interface BrowserRuntimeConfig {
  launchpadUrls: LaunchpadRuntimeUrls;
}

export const UNAVAILABLE_LAUNCHPAD_RUNTIME_URLS: LaunchpadRuntimeUrls =
  Object.freeze({
    lingxi: null,
    sop_assistant: null,
    word_translate: null,
    word_review: null,
  });

function objectValue(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("browser_runtime_config_invalid");
  }
  return value as Record<string, unknown>;
}

function requireExactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
): void {
  const actual = Object.keys(value).sort();
  const required = [...expected].sort();
  if (
    actual.length !== required.length ||
    actual.some((key, index) => key !== required[index])
  ) {
    throw new Error("browser_runtime_config_invalid");
  }
}

function normalizeBrowserPublicUrl(value: unknown): string | null {
  if (value === null) return null;
  if (
    typeof value !== "string" ||
    value !== value.trim() ||
    value.length === 0 ||
    value.length > 2048 ||
    /\s/.test(value)
  ) {
    throw new Error("browser_runtime_config_invalid");
  }

  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error("browser_runtime_config_invalid");
  }
  if (
    !["http:", "https:"].includes(parsed.protocol) ||
    !parsed.hostname ||
    parsed.username ||
    parsed.password ||
    parsed.search ||
    parsed.hash.includes("=")
  ) {
    throw new Error("browser_runtime_config_invalid");
  }
  return value;
}

export function normalizeBrowserRuntimeConfig(
  value: unknown,
): BrowserRuntimeConfig {
  const root = objectValue(value);
  requireExactKeys(root, ["launchpad_urls"]);
  const launchpadUrlsWire = objectValue(root.launchpad_urls);
  requireExactKeys(launchpadUrlsWire, LAUNCHPAD_RUNTIME_URL_KEYS);

  const launchpadUrls = Object.fromEntries(
    LAUNCHPAD_RUNTIME_URL_KEYS.map((key) => [
      key,
      normalizeBrowserPublicUrl(launchpadUrlsWire[key]),
    ]),
  ) as Record<LaunchpadRuntimeUrlKey, string | null>;

  return { launchpadUrls };
}

export async function fetchBrowserRuntimeConfig(
  options: { signal?: AbortSignal } = {},
): Promise<BrowserRuntimeConfig> {
  const response = await authFetch<unknown>(
    `${API_BASE}/api/runtime-config/browser`,
    {
      cache: "no-store",
      signal: options.signal,
    },
  );
  return normalizeBrowserRuntimeConfig(response);
}
