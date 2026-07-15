import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const useAuthSource = readFileSync(
  new URL("../useAuth.tsx", import.meta.url),
  "utf8",
);

test("useAuth bootstraps browser auth from the backend cookie session probe", () => {
  assert.match(useAuthSource, /const hadSessionMarker = !!getAccessToken\(\);/);
  assert.match(
    useAuthSource,
    /const currentUser = await authApi\.getCurrentUser\(\{[\s\S]*signal: owner\.abortController\.signal/,
  );
  assert.match(
    useAuthSource,
    /if \(!hadSessionMarker\) \{[\s\S]*setTokens\("cookie-session"\);[\s\S]*applyAuthenticatedUser\(currentUser, owner\)/,
  );
  assert.doesNotMatch(
    useAuthSource,
    /if \(!getAccessToken\(\)\) \{[\s\S]*authApi\.getCurrentUser/s,
  );
});

test("useAuth listens for cross-tab cookie-session marker changes", () => {
  assert.match(useAuthSource, /window\.addEventListener\("storage", handleStorage\)/);
  assert.match(
    useAuthSource,
    /const handleStorage = \(event: StorageEvent\) => \{[\s\S]*handleBrowserAuthStorageEvent\(event, refreshUser\);[\s\S]*};/,
  );
  assert.match(
    useAuthSource,
    /const applyAuthenticatedUser = useCallback\([\s\S]*if \(!isCurrentAuthOperation\(owner\)\) return false;/,
  );
  assert.match(
    useAuthSource,
    /const beginAuthOperation = useCallback\([\s\S]*invalidateAuthOperation\(\);[\s\S]*new AbortController\(\)/,
  );
});

test("useAuth rolls back the backend session when login or OAuth hydration fails", () => {
  assert.match(
    useAuthSource,
    /let sessionEstablished = false;[\s\S]*sessionEstablished = true;[\s\S]*if \(sessionEstablished\) \{[\s\S]*await authApi\.logout\(owner\.abortController\.signal\);[\s\S]*throw error;/,
  );
});

test("useAuth login resumes the redirect path saved by revoked-session handling", () => {
  assert.match(
    useAuthSource,
    /const redirectPath = getRedirectPath\(\);[\s\S]*if \(redirectPath\) \{[\s\S]*clearRedirectPath\(\);[\s\S]*}[\s\S]*return completedAuthOperation\(redirectPath \?\? null\);/,
  );
});

test("useAuth exposes explicit cancellation instead of null or void success sentinels", () => {
  assert.match(
    useAuthSource,
    /export type AuthOperationOutcome<[\s\S]*status: "cancelled"/,
  );
  assert.match(
    useAuthSource,
    /if \(!isCurrentAuthOperation\(owner\)\) return cancelledAuthOperation\(\);/,
  );
  assert.doesNotMatch(
    useAuthSource,
    /if \(!isCurrentAuthOperation\(owner\)\) return null;/,
  );
});
