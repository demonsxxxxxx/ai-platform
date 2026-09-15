import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import "../../../i18n";
import type { MCPServerResponse } from "../../../types";
import { MCPServerForm } from "../MCPServerForm";

for (const transport of [undefined, "sandbox"] as const) {
  test(`MCP form exposes network configuration and safely migrates ${transport ?? "new"} server`, () => {
    const server: MCPServerResponse | undefined = transport ? {
      name: "legacy", transport, enabled: true, is_system: false, can_edit: true,
      allowed_roles: [], role_quotas: {},
    } : undefined;
    const html = renderToStaticMarkup(createElement(MCPServerForm, {
      server, onSave: async () => true, onCancel: () => {},
    }));
    assert.match(html, /type="url"/);
    assert.doesNotMatch(html, /placeholder="[^"]*npx/);
    assert.doesNotMatch(html, /value="sandbox"/);
    if (transport) assert.match(html, /命令传输尚不受支持/);
  });
}
