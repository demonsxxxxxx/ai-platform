import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { TFunction } from "i18next";
import { translateBackendError } from "../backendErrors.ts";

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
