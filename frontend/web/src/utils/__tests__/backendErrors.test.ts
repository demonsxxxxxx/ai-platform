import test from "node:test";
import assert from "node:assert/strict";
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
    translateBackendError("persona_preset_no_delete_permission", t),
    "translated:personaPresets.noDeletePermission",
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

test("returns unknown backend messages unchanged", () => {
  assert.equal(
    translateBackendError("unexpected_backend_error", t),
    "unexpected_backend_error",
  );
});
