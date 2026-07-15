import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { TFunction } from "i18next";
import {
  projectSafeBackendError,
  translateBackendError,
} from "../backendErrors.ts";

const t = ((key: string, options?: { permission?: string }) =>
  options?.permission
    ? `translated:${key}:${options.permission}`
    : `translated:${key}`) as TFunction;

test("translates shared backend error codes", () => {
  assert.equal(
    translateBackendError("model_not_found", t),
    "translated:errors.modelNotFound",
  );
  assert.equal(
    translateBackendError("File not found", t),
    "translated:backendErrors.fileNotFound",
  );
});

test("translates backend error patterns", () => {
  assert.equal(
    translateBackendError("缺少权限: model:admin", t),
    "translated:backendErrors.permissionMissing:model:admin",
  );
});

test("translates fail-closed public governance contract codes", () => {
  assert.equal(
    translateBackendError("skill_file_write_contract_not_backed", t),
    "translated:backendErrors.skillFileWriteNotBacked",
  );
  assert.equal(
    translateBackendError("skill_file_delete_contract_not_backed", t),
    "translated:backendErrors.skillFileDeleteNotBacked",
  );
  assert.equal(
    translateBackendError("skill_import_contract_not_backed", t),
    "translated:backendErrors.skillImportNotBacked",
  );
  assert.equal(
    translateBackendError("marketplace_direct_write_contract_not_backed", t),
    "translated:backendErrors.marketplaceDirectWriteNotBacked",
  );
  assert.equal(
    translateBackendError("mcp_lifecycle_contract_not_backed", t),
    "translated:backendErrors.mcpLifecycleNotBacked",
  );
});

test("all shipped locales include fail-closed governance error copy", () => {
  const requiredKeys = [
    "skillFileWriteNotBacked",
    "skillFileDeleteNotBacked",
    "skillImportNotBacked",
    "marketplaceDirectWriteNotBacked",
    "mcpLifecycleNotBacked",
  ];

  for (const locale of ["en", "zh", "ja", "ko", "ru"]) {
    const messages = JSON.parse(
      readFileSync(join(process.cwd(), `src/i18n/locales/${locale}.json`), "utf8"),
    );
    for (const key of requiredKeys) {
      assert.equal(
        typeof messages.backendErrors[key],
        "string",
        `${locale}.${key}`,
      );
      assert.ok(messages.backendErrors[key].length > 12, `${locale}.${key}`);
    }
  }
});

test("returns unknown backend messages unchanged", () => {
  assert.equal(
    translateBackendError("unexpected_backend_error", t),
    "unexpected_backend_error",
  );
});

test("safe error projection translates only allowlisted exact detail or code", () => {
  assert.deepEqual(projectSafeBackendError("invalid_credentials", 401, t), {
    code: "invalid_credentials",
    message: "translated:backendErrors.invalidCredentials",
  });
  assert.deepEqual(
    projectSafeBackendError(
      { code: "invalid_credentials", message: "token=private" },
      401,
      t,
    ),
    {
      code: "invalid_credentials",
      message: "translated:backendErrors.invalidCredentials",
    },
  );
  assert.deepEqual(
    projectSafeBackendError("unexpected_backend_error", 500, t),
    {
      code: "unexpected_backend_error",
      message: "translated:chat.requestFailed",
    },
  );
});

test("safe error projection rejects private diagnostics and uses status-localized fallbacks", () => {
  const privateValues: unknown[] = [
    "C:\\private\\agent.log?token=secret",
    "<html>proxy diagnostic</html>",
    { message: "Bearer private-token" },
    { detail: { code: "invalid_credentials" } },
    { code: "a".repeat(65) },
    ["invalid_credentials"],
  ];
  const expectedKeys = new Map([
    [401, "backendErrors.unauthenticated"],
    [403, "errors.noPermission"],
    [429, "backendErrors.tooManyRequests"],
    [500, "chat.requestFailed"],
  ]);

  for (const [status, key] of expectedKeys) {
    for (const detail of privateValues) {
      const projection = projectSafeBackendError(detail, status, t);
      assert.equal(projection.code, undefined);
      assert.equal(projection.message, `translated:${key}`);
      assert.doesNotMatch(projection.message, /private|token|proxy|html/i);
    }
  }
});
