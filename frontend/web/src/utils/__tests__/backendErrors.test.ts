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

test("projects stable Skill package failures to actionable Skill copy", () => {
  const expected = new Map([
    ["skill_package_invalid_zip", "backendErrors.skillPackageInvalidZip"],
    ["skill_package_skill_md_required", "backendErrors.skillPackageSkillMdRequired"],
    ["skill_package_multiple_skills_not_supported", "backendErrors.skillPackageMultipleSkills"],
    ["skill_package_description_required", "backendErrors.skillPackageDescriptionRequired"],
    ["skill_package_too_large", "backendErrors.skillPackageTooLarge"],
  ]);

  for (const [code, key] of expected) {
    assert.deepEqual(projectSafeBackendError(code, 400, t), {
      code,
      message: `translated:${key}`,
    });
  }
});

test("all shipped locales include fail-closed governance error copy", () => {
  const requiredKeys = [
    "skillFileWriteNotBacked",
    "skillFileDeleteNotBacked",
    "skillImportNotBacked",
    "marketplaceDirectWriteNotBacked",
    "mcpLifecycleNotBacked",
    "skillPackageInvalidZip",
    "skillPackageSkillMdRequired",
    "skillPackageMultipleSkills",
    "skillPackageDescriptionRequired",
    "skillPackageTooLarge",
    "skillPackageUnsafe",
    "skillPackageInvalidText",
    "skillPackageNameMismatch",
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

test("projects unknown backend messages to generic localized copy", () => {
  assert.equal(
    translateBackendError("unexpected_backend_error", t),
    "translated:chat.requestFailed",
  );
  for (const diagnostic of [
    "C:\\private\\worker.log?token=secret",
    "<html>proxy bearer=secret</html>",
    "missing_permission:../../private-token",
    "missing_permission:Bearer private-token",
    "No permission to upload secret-token files",
  ]) {
    const message = translateBackendError(diagnostic, t);
    assert.equal(message, "translated:chat.requestFailed");
    assert.doesNotMatch(message, /private|token|proxy|html|worker/i);
  }
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

test("projects company auth rejection to specific safe Chinese guidance", () => {
  const message =
    "公司账号登录失败，请检查账号或密码；如仍无法登录，请联系管理员确认账号已开通。";

  assert.deepEqual(projectSafeBackendError("company_login_failed", 401, t), {
    code: "company_login_failed",
    message,
  });
  assert.deepEqual(
    projectSafeBackendError(
      { code: "company_login_failed", message: "password=private" },
      401,
      t,
    ),
    {
      code: "company_login_failed",
      message,
    },
  );
  assert.equal(translateBackendError("company_login_failed", t), message);
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
