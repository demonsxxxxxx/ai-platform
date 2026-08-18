import test from "node:test";
import assert from "node:assert/strict";
import {
  MCP_AUTH_MESSAGE,
  MCP_GATEWAY_AUTH_CHANGED_EVENT,
  MCP_GATEWAY_JWT_STORAGE_KEY,
  clearMcpGatewayJwt,
  getMcpGatewayJwt,
  isMcpAuthHandoffMessage,
  setMcpGatewayJwt,
} from "../mcpGatewayAuth.ts";

test("MCP JWT storage is isolated under its dedicated localStorage key", () => {
  const values = new Map<string, string>();
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
    },
  });

  setMcpGatewayJwt("  company.jwt  ");
  assert.equal(values.get(MCP_GATEWAY_JWT_STORAGE_KEY), "company.jwt");
  assert.equal(getMcpGatewayJwt(), "company.jwt");
  assert.equal(values.has("access_token"), false);
  clearMcpGatewayJwt();
  assert.equal(getMcpGatewayJwt(), null);
});

test("handoff accepts only the exact message type, nonce, and non-empty token", () => {
  const nonce = "a".repeat(64);
  assert.equal(
    isMcpAuthHandoffMessage(
      { type: MCP_AUTH_MESSAGE, nonce, token: "company.jwt" },
      nonce,
    ),
    true,
  );
  assert.equal(
    isMcpAuthHandoffMessage(
      { type: MCP_AUTH_MESSAGE, nonce: "b".repeat(64), token: "company.jwt" },
      nonce,
    ),
    false,
  );
  assert.equal(
    isMcpAuthHandoffMessage({ type: "other", nonce, token: "company.jwt" }, nonce),
    false,
  );
  assert.equal(
    isMcpAuthHandoffMessage({ type: MCP_AUTH_MESSAGE, nonce, token: " " }, nonce),
    false,
  );
});

test("credential changes notify same-tab catalog consumers", () => {
  const target = new EventTarget();
  const originalWindow = globalThis.window;
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: target,
  });
  let changes = 0;
  target.addEventListener(MCP_GATEWAY_AUTH_CHANGED_EVENT, () => {
    changes += 1;
  });

  try {
    setMcpGatewayJwt("company.jwt");
    clearMcpGatewayJwt();
    assert.equal(changes, 2);
  } finally {
    Object.defineProperty(globalThis, "window", {
      configurable: true,
      value: originalWindow,
    });
  }
});
