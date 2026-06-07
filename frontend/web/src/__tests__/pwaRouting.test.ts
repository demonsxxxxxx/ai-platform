import test from "node:test";
import assert from "node:assert/strict";
import { getPwaRequestKind, isBackendPath } from "../pwaRouting.ts";

const ORIGIN = "https://lambchat.com";

test("bypasses backend, streaming, non-GET, and cross-origin requests", () => {
  assert.equal(isBackendPath("/api/chat"), true);
  assert.equal(isBackendPath("/ws/session"), false);
  assert.equal(isBackendPath("/human/pending"), false);
  assert.equal(isBackendPath("/tools"), false);
  assert.equal(isBackendPath("/default/stream"), true);

  assert.equal(
    getPwaRequestKind({
      method: "POST",
      mode: "cors",
      url: `${ORIGIN}/chat`,
      scopeOrigin: ORIGIN,
      accept: "text/html",
    }),
    "bypass",
  );
  assert.equal(
    getPwaRequestKind({
      method: "GET",
      mode: "cors",
      url: `${ORIGIN}/api/chat`,
      scopeOrigin: ORIGIN,
      accept: "application/json",
    }),
    "bypass",
  );
  assert.equal(
    getPwaRequestKind({
      method: "GET",
      mode: "cors",
      url: `${ORIGIN}/chat/stream`,
      scopeOrigin: ORIGIN,
      accept: "text/event-stream",
    }),
    "bypass",
  );
  assert.equal(
    getPwaRequestKind({
      method: "GET",
      mode: "cors",
      url: "https://fonts.gstatic.com/font.woff2",
      scopeOrigin: ORIGIN,
      accept: "font/woff2",
    }),
    "bypass",
  );
});

test("classifies SPA navigations and static assets for offline handling", () => {
  assert.equal(
    getPwaRequestKind({
      method: "GET",
      mode: "navigate",
      url: `${ORIGIN}/chat/session-id`,
      scopeOrigin: ORIGIN,
      accept: "text/html",
    }),
    "navigation",
  );
  assert.equal(
    getPwaRequestKind({
      method: "GET",
      mode: "cors",
      url: `${ORIGIN}/assets/index.abc123.js?v=1`,
      scopeOrigin: ORIGIN,
      accept: "text/javascript",
    }),
    "static-asset",
  );
  assert.equal(
    getPwaRequestKind({
      method: "GET",
      mode: "cors",
      url: `${ORIGIN}/icons/icon.svg`,
      scopeOrigin: ORIGIN,
      accept: "image/svg+xml",
    }),
    "static-asset",
  );
});
