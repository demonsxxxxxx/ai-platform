import assert from "node:assert/strict";
import test from "node:test";

import {
  MCP_JWT_HEADER_NAME,
  validateMcpStaticHeaderNames,
} from "../mcpHeaderValidation.ts";

test("rejects the dynamic JWT header under casing and whitespace variants", () => {
  for (const key of [
    MCP_JWT_HEADER_NAME,
    "jwt-authorization",
    " Jwt-Authorization ",
  ]) {
    assert.equal(
      validateMcpStaticHeaderNames([{ key }]),
      "mcp_header_conflict",
    );
  }
});

test("enterprise Gateway reserves user identity headers without changing generic MCP", () => {
  for (const key of ["Authorization", "authorization", "Cookie", "X-MCP-Username", "X-MCP-Password"]) {
    assert.equal(validateMcpStaticHeaderNames([{ key }], "enterprise_gateway"), "mcp_header_conflict");
  }
  assert.equal(validateMcpStaticHeaderNames([{ key: "Authorization" }]), null);
  assert.equal(validateMcpStaticHeaderNames([{ key: "X-Trace-Id" }], "enterprise_gateway"), null);
});

test("rejects case-insensitive duplicates and accepts distinct static headers", () => {
  assert.equal(
    validateMcpStaticHeaderNames([{ key: "X-Api-Key" }, { key: "x-api-key" }]),
    "mcp_header_duplicate",
  );
  assert.equal(
    validateMcpStaticHeaderNames([{ key: "X-Api-Key" }, { key: "X-Tenant" }]),
    null,
  );
});
