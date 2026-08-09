import assert from "node:assert/strict";
import test from "node:test";

import {
  LOCAL_AUTH_PROXY_STRIPPED_HEADERS,
  localDevAuthBootstrapResponse,
  resolveLocalDevAuthProxy,
} from "./dev-auth-proxy";

const loopbackTarget = "http://127.0.0.1:18020";

test("local auth proxy is disabled unless explicitly enabled for vite serve", () => {
  assert.equal(
    resolveLocalDevAuthProxy({
      apiTarget: loopbackTarget,
      command: "serve",
      env: {},
    }),
    null,
  );
  assert.equal(
    resolveLocalDevAuthProxy({
      apiTarget: loopbackTarget,
      command: "build",
      env: { AI_PLATFORM_LOCAL_AUTH_PROXY_ENABLED: "true" },
    }),
    null,
  );
});

test("local auth proxy defaults to a loopback-only ordinary user", () => {
  const config = resolveLocalDevAuthProxy({
    apiTarget: loopbackTarget,
    command: "serve",
    env: { AI_PLATFORM_LOCAL_AUTH_PROXY_ENABLED: "true" },
  });

  assert.deepEqual(config, {
    headers: {
      "X-AI-User-ID": "local-dev-user",
      "X-AI-Tenant-ID": "default",
      "X-AI-Roles": "user",
    },
    serverHost: "127.0.0.1",
  });
  assert.equal("X-AI-Gateway-Secret" in config!.headers, false);
  assert.equal("X-AI-Permissions" in config!.headers, false);
  assert.equal(LOCAL_AUTH_PROXY_STRIPPED_HEADERS.includes("X-AI-Permissions"), true);
  assert.equal(
    LOCAL_AUTH_PROXY_STRIPPED_HEADERS.includes("X-AI-Gateway-Secret"),
    true,
  );
});

test("local auth proxy permits explicit admin and department fixtures", () => {
  const config = resolveLocalDevAuthProxy({
    apiTarget: "http://localhost:8020",
    command: "serve",
    env: {
      AI_PLATFORM_LOCAL_AUTH_PROXY_ENABLED: "TRUE",
      AI_PLATFORM_LOCAL_AUTH_USER_ID: "market-admin",
      AI_PLATFORM_LOCAL_AUTH_TENANT_ID: "default",
      AI_PLATFORM_LOCAL_AUTH_DEPARTMENT_ID: "dept-42",
      AI_PLATFORM_LOCAL_AUTH_ROLE: "ADMIN",
    },
  });

  assert.deepEqual(config?.headers, {
    "X-AI-User-ID": "market-admin",
    "X-AI-Tenant-ID": "default",
    "X-AI-Roles": "admin",
    "X-AI-Department-ID": "dept-42",
  });
});

test("local auth proxy rejects non-loopback targets", () => {
  assert.throws(
    () =>
      resolveLocalDevAuthProxy({
        apiTarget: "http://10.56.0.211:18020",
        command: "serve",
        env: { AI_PLATFORM_LOCAL_AUTH_PROXY_ENABLED: "true" },
      }),
    /loopback API address/,
  );
});

test("local auth proxy rejects unsafe principals and elevated role aliases", () => {
  for (const env of [
    {
      AI_PLATFORM_LOCAL_AUTH_PROXY_ENABLED: "true",
      AI_PLATFORM_LOCAL_AUTH_USER_ID: "bad user",
    },
    {
      AI_PLATFORM_LOCAL_AUTH_PROXY_ENABLED: "true",
      AI_PLATFORM_LOCAL_AUTH_ROLE: "developer",
    },
  ]) {
    assert.throws(() =>
      resolveLocalDevAuthProxy({
        apiTarget: loopbackTarget,
        command: "serve",
        env,
      }),
    );
  }
});

test("local auth bootstrap accepts only the bounded public nonce contract", () => {
  const nonce = "a".repeat(43);
  assert.deepEqual(localDevAuthBootstrapResponse({ nonce }), {
    status: "ready",
    protocol_version: 1,
  });
  assert.deepEqual(
    localDevAuthBootstrapResponse({
      nonce,
      protocol_version: 2,
      browser_incarnation: "b".repeat(43),
      generation: 7,
      rotation_ticket: "c".repeat(43),
      recovery_only: false,
    }),
    { status: "ready", protocol_version: 2, generation: 7 },
  );

  for (const payload of [
    null,
    {},
    { nonce: "short" },
    { nonce, protocol_version: 1, generation: 1 },
    { nonce, protocol_version: 2, browser_incarnation: "b".repeat(43) },
    { nonce, protocol_version: 2, browser_incarnation: "bad value", generation: 1 },
    {
      nonce,
      protocol_version: 2,
      browser_incarnation: "b".repeat(43),
      generation: 1,
      rotation_ticket: "bad",
    },
    { nonce, unexpected: true },
  ]) {
    assert.equal(localDevAuthBootstrapResponse(payload), null);
  }
});

test("local auth proxy rejects non-http loopback target schemes", () => {
  assert.throws(
    () =>
      resolveLocalDevAuthProxy({
        apiTarget: "ftp://127.0.0.1:8020",
        command: "serve",
        env: { AI_PLATFORM_LOCAL_AUTH_PROXY_ENABLED: "true" },
      }),
    /HTTP or HTTPS/,
  );
});
