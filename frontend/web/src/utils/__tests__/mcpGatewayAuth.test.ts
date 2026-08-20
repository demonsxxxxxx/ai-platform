import assert from "node:assert/strict";
import test from "node:test";
import { MCP_GATEWAY_CREDENTIAL_OWNER } from "../mcpGatewayAuth.ts";

test("MCP gateway credentials are backend-owned", () => {
  assert.equal(MCP_GATEWAY_CREDENTIAL_OWNER, "backend");
});
