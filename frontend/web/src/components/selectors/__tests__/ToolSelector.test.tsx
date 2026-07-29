import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import type { ChatMcpCatalogState } from "../../../hooks/useTools.ts";
import { getChatMcpCatalogNotice } from "../ToolSelector.tsx";

const zh = JSON.parse(
  readFileSync(new URL("../../../i18n/locales/zh.json", import.meta.url), "utf8"),
) as Record<string, unknown>;

function localized(key: string): string {
  return key.split(".").reduce<unknown>((value, segment) => {
    if (typeof value !== "object" || value === null || !(segment in value)) {
      throw new Error(`missing_translation:${key}`);
    }
    return (value as Record<string, unknown>)[segment];
  }, zh) as string;
}

function catalog(status: ChatMcpCatalogState["status"], unavailable: string[] = []) {
  return { status, unavailable } as ChatMcpCatalogState;
}

test("renders bounded Chinese catalog states and exposes retry only for errors", () => {
  const loading = getChatMcpCatalogNotice(catalog("loading"));
  const empty = getChatMcpCatalogNotice(catalog("empty"));
  const error = getChatMcpCatalogNotice(catalog("error"));
  const degraded = getChatMcpCatalogNotice(
    catalog("degraded", [
      "tools.catalog.unavailable.discoveryFailed",
      "tools.catalog.unavailable.generic",
      "tools.catalog.unavailable.discoveryFailed",
      "tools.catalog.unavailable.noTools",
    ]),
  );

  assert.equal(localized(loading?.titleKey ?? ""), "正在加载 MCP 工具目录…");
  assert.equal(localized(empty?.titleKey ?? ""), "当前没有可选择的 MCP 工具");
  assert.equal(localized(error?.titleKey ?? ""), "MCP 工具目录暂时无法加载");
  assert.equal(error?.retryable, true);
  assert.equal(degraded?.retryable, false);
  assert.equal(localized(degraded?.titleKey ?? ""), "部分 MCP 工具暂不可用");
  assert.deepEqual(degraded?.detailKeys, [
    "tools.catalog.unavailable.discoveryFailed",
    "tools.catalog.unavailable.generic",
    "tools.catalog.unavailable.noTools",
  ]);
  assert.equal(getChatMcpCatalogNotice(catalog("ready")), null);
});
