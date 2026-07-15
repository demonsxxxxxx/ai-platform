import assert from "node:assert/strict";
import test from "node:test";

import { AUTH_SESSION_MARKER_KEY } from "../../services/api/token.ts";
import { classifyBrowserAuthStorageEvent } from "../browserAuthStorage.ts";

test("browser auth storage classification distinguishes login, replacement, and logout", () => {
  assert.deepEqual(
    classifyBrowserAuthStorageEvent({
      key: AUTH_SESSION_MARKER_KEY,
      oldValue: null,
      newValue: "marker-first",
    } as StorageEvent),
    { type: "login", marker: "marker-first" },
  );
  assert.deepEqual(
    classifyBrowserAuthStorageEvent({
      key: AUTH_SESSION_MARKER_KEY,
      oldValue: "marker-a",
      newValue: "marker-b",
    } as StorageEvent),
    { type: "replacement", marker: "marker-b" },
  );
  assert.deepEqual(
    classifyBrowserAuthStorageEvent({
      key: AUTH_SESSION_MARKER_KEY,
      oldValue: "marker-b",
      newValue: null,
    } as StorageEvent),
    { type: "logout" },
  );
});

test("browser auth storage classification ignores unrelated and unchanged markers", () => {
  assert.equal(
    classifyBrowserAuthStorageEvent({
      key: "access_token",
      oldValue: "legacy",
      newValue: null,
    } as StorageEvent),
    null,
  );
  assert.equal(
    classifyBrowserAuthStorageEvent({
      key: AUTH_SESSION_MARKER_KEY,
      oldValue: "same-marker",
      newValue: "same-marker",
    } as StorageEvent),
    null,
  );
});
