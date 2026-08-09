import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

function readViteConfig(): string {
  return readFileSync(new URL("../../vite.config.ts", import.meta.url), "utf8");
}

test("dev proxy defaults to ai-platform API instead of the old LambChat backend", () => {
  const source = readViteConfig();

  assert.match(source, /127\.0\.0\.1:8020/);
  assert.match(source, /VITE_AI_PLATFORM_API_TARGET/);
  assert.doesNotMatch(source, /VITE_API_TARGET/);
  assert.doesNotMatch(source, /127\.0\.0\.1:8000/);
});

test("dev proxy does not expose legacy LambChat backend route proxies", () => {
  const source = readViteConfig();

  assert.doesNotMatch(source, /AGENT_IDS/);
  assert.doesNotMatch(source, /"\/human"/);
  assert.doesNotMatch(source, /"\/tools"/);
  assert.doesNotMatch(source, /"\/ws"/);
  assert.doesNotMatch(source, /Object\.fromEntries/);
});

test("local auth stays in the node-side dev proxy and is fail-closed", () => {
  const source = readViteConfig();
  const helper = readFileSync(
    new URL("../../dev-auth-proxy.ts", import.meta.url),
    "utf8",
  );

  assert.match(source, /resolveLocalDevAuthProxy/);
  assert.match(source, /localDevAuthBootstrapPlugin\(localDevAuthProxy\)/);
  assert.match(source, /LOCAL_AUTH_BOOTSTRAP_MAX_BYTES = 4096/);
  assert.match(source, /Cache-Control", "no-store/);
  assert.match(source, /localDevAuthProxy\?\.serverHost \?\? true/);
  assert.match(source, /LOCAL_AUTH_PROXY_STRIPPED_HEADERS/);
  assert.match(source, /proxyReq\.removeHeader\(name\)/);
  assert.match(source, /proxyReq\.setHeader\(name, value\)/);
  assert.match(helper, /command !== "serve"/);
  assert.match(helper, /loopback API address/);
  assert.match(helper, /serverHost: "127\.0\.0\.1"/);
  assert.match(helper, /"X-AI-Gateway-Secret"/);
  assert.match(helper, /"X-AI-Permissions"/);
});
