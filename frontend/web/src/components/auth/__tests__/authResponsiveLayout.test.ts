import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const currentDir = dirname(fileURLToPath(import.meta.url));

function readAuthSource(fileName: string): string {
  return readFileSync(join(currentDir, "..", fileName), "utf8");
}

test("auth pages use safe centered mobile layout classes", () => {
  const authPage = readAuthSource("AuthPage.tsx");
  const authLayout = readAuthSource("AuthLayout.tsx");
  const forgotPassword = readAuthSource("ForgotPassword.tsx");
  const resetPassword = readAuthSource("ResetPassword.tsx");

  assert.equal(authPage.includes("max-wfull"), false);
  assert.equal(authLayout.includes("max-wfull"), false);
  assert.equal(forgotPassword.includes("max-wfull"), false);
  assert.equal(resetPassword.includes("max-wfull"), false);
  assert.equal(authPage.includes("auth-crosshatch"), true);
  assert.equal(authPage.includes("min-h-[100dvh]"), true);
});

test("login page hides self-service account and GitHub footer links", () => {
  const authPage = readAuthSource("AuthPage.tsx");

  assert.equal(authPage.includes("switchMode"), false);
  assert.equal(authPage.includes("auth.registrationDisabled"), false);
  assert.equal(authPage.includes("auth.registerNow"), false);
  assert.equal(authPage.includes("auth.forgotPassword"), false);
  assert.equal(authPage.includes("/auth/reset-request"), false);
  assert.equal(authPage.includes("GITHUB_URL"), false);
  assert.equal(authPage.includes("<span>GitHub</span>"), false);
});
