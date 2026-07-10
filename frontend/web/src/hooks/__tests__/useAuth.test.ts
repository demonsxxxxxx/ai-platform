import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const useAuthSource = readFileSync(
  new URL("../useAuth.tsx", import.meta.url),
  "utf8",
);

test("useAuth bootstraps browser auth from the backend cookie session probe", () => {
  assert.match(useAuthSource, /const hadSessionMarker = !!getAccessToken\(\);/);
  assert.match(useAuthSource, /const currentUser = await authApi\.getCurrentUser\(\);/);
  assert.match(
    useAuthSource,
    /if \(!hadSessionMarker\) \{[\s\S]*setTokens\("cookie-session"\);[\s\S]*}\s*applyAuthenticatedUser\(currentUser\);[\s\S]*if \(!hadSessionMarker\) \{[\s\S]*new CustomEvent\("auth:login"\)/,
  );
  assert.doesNotMatch(
    useAuthSource,
    /if \(!getAccessToken\(\)\) \{[\s\S]*authApi\.getCurrentUser/s,
  );
});

test("useAuth listens for cross-tab cookie-session marker changes", () => {
  assert.match(useAuthSource, /window\.addEventListener\("storage", handleStorage\)/);
  assert.match(useAuthSource, /const authEvent = parseAuthStorageEvent\(event\);/);
  assert.match(
    useAuthSource,
    /if \(authEvent === "logout"\) \{[\s\S]*clearLocalAuthView\(\);[\s\S]*return;[\s\S]*}/,
  );
  assert.match(
    useAuthSource,
    /if \(authEvent === "login"\) \{[\s\S]*void refreshUser\(\);[\s\S]*}/,
  );
  const applyAuthenticatedUserBlock = useAuthSource.match(
    /const applyAuthenticatedUser = useCallback\(\(currentUser: User\) => \{[\s\S]*?\}, \[\]\);/,
  );
  assert.ok(applyAuthenticatedUserBlock);
  assert.doesNotMatch(applyAuthenticatedUserBlock[0], /setTokens\("cookie-session"\)/);
});

test("useAuth rolls back the backend session when login or OAuth hydration fails", () => {
  assert.match(useAuthSource, /const rollbackServerSession = useCallback\(async \(\) => \{/);
  assert.match(useAuthSource, /await authApi\.logout\(\);/);
  assert.match(
    useAuthSource,
    /const currentUser = await authApi\.getCurrentUser\(\);[\s\S]*} catch \(error\) \{[\s\S]*await rollbackServerSession\(\);[\s\S]*throw error;[\s\S]*}/,
  );
});
