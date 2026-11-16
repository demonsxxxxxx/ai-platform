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

function catalog(status: ChatMcpCatalogState["status"], unavailable: ChatMcpCatalogState["unavailable"] = []) {
  return { status, unavailable } as ChatMcpCatalogState;
}

test("renders compact Chinese catalog states and exposes retry for partial and full failures", () => {
  const loading = getChatMcpCatalogNotice(catalog("loading"));
  const empty = getChatMcpCatalogNotice(catalog("empty"));
  const error = getChatMcpCatalogNotice(catalog("error"));
  const degraded = getChatMcpCatalogNotice(
    catalog("degraded", [
      { label: "gateway", reasonKey: "tools.catalog.unavailable.discoveryFailed" },
      { label: "direct", reasonKey: "tools.catalog.unavailable.noTools" },
    ]),
  );

  assert.equal(localized(loading?.titleKey ?? ""), "正在加载 MCP 工具目录…");
  assert.equal(localized(empty?.titleKey ?? ""), "当前没有可选择的 MCP 工具");
  assert.equal(localized(error?.titleKey ?? ""), "MCP 工具目录暂时无法加载");
  assert.equal(error?.retryable, true);
  assert.equal(degraded?.retryable, true);
  assert.equal(localized(degraded?.titleKey ?? ""), "有 {{count}} 个 MCP 服务未提供可用工具。");
  assert.equal(localized(getChatMcpCatalogNotice(catalog("degraded", [
    { label: "gateway", reasonKey: "tools.catalog.unavailable.discoveryFailed" },
  ]))!.titleKey), "有 {{count}} 个 MCP 服务未加载成功。");
  assert.equal(getChatMcpCatalogNotice(catalog("ready")), null);
});

test("MCP loading state has one catalog context owner across the composer", () => {
  const selector = readFileSync(new URL("../ToolSelector.tsx", import.meta.url), "utf8");
  assert.match(selector, /useContext\(ChatMcpCatalogContext\)/);
  assert.doesNotMatch(selector, /isLoading\??:/);
  for (const path of [
    "../../chat/ChatInput.tsx",
    "../../chat/chatInputTypes.ts",
    "../../layout/AppContent/ChatView.tsx",
    "../../layout/AppContent/ChatAppContent.tsx",
  ]) {
    const source = readFileSync(new URL(path, import.meta.url), "utf8");
    assert.doesNotMatch(source, /\btoolsLoading\b/, path);
  }
});
