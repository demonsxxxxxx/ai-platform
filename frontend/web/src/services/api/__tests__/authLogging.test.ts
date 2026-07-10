import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const browserAuthSources = [
  readFileSync(new URL("../auth.ts", import.meta.url), "utf8"),
  readFileSync(new URL("../fetch.ts", import.meta.url), "utf8"),
  readFileSync(new URL("../authenticatedRequest.ts", import.meta.url), "utf8"),
  readFileSync(new URL("../token.ts", import.meta.url), "utf8"),
  readFileSync(new URL("../tokenManager.ts", import.meta.url), "utf8"),
  readFileSync(new URL("../../../hooks/useAuth.tsx", import.meta.url), "utf8"),
].join("\n");

test("browser auth source files do not log tokens or authorization secrets", () => {
  assert.doesNotMatch(
    browserAuthSources,
    /console\.(?:log|info|debug|warn|error)\([^)]*(?:access_token|refresh_token|authorization|bearer)/i,
  );
});
