import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const testDirectory = fileURLToPath(new URL(".", import.meta.url));
const errorKeys = [
  "serverFileTooLarge",
  "serverUnsupportedFileType",
  "uploadFailedRecoverable",
] as const;

const expected = {
  en: {
    serverFileTooLarge:
      "This file exceeds the 50 MB limit. Compress it or choose a smaller file and try again.",
    serverUnsupportedFileType:
      "This file format or content is unsafe and cannot be uploaded. Choose a safe, common file format instead.",
    uploadFailedRecoverable:
      "The file was not uploaded. Check your connection and try again.",
  },
  zh: {
    serverFileTooLarge: "文件超过 50 MB 上限，请压缩或选择更小的文件后重试",
    serverUnsupportedFileType:
      "该文件格式或内容不安全，无法上传。请改用安全的常用文件格式",
    uploadFailedRecoverable: "文件上传未完成，请检查网络后重试",
  },
} as const;

type LocaleName = keyof typeof expected;
type FileUploadLocale = Partial<
  Record<(typeof errorKeys)[number] | "uploadFailed", string>
>;

function readFileUploadLocale(locale: LocaleName): FileUploadLocale {
  const contents = JSON.parse(
    readFileSync(resolve(testDirectory, `../locales/${locale}.json`), "utf8"),
  ) as { fileUpload?: FileUploadLocale };
  assert.ok(contents.fileUpload, `${locale} must provide fileUpload translations`);
  return contents.fileUpload;
}

test("upload request errors have exact, paired English and Chinese copy", () => {
  for (const localeName of Object.keys(expected) as LocaleName[]) {
    const locale = readFileUploadLocale(localeName);
    const values = errorKeys.map((key) => {
      const value = locale[key] ?? "";
      assert.equal(value, expected[localeName][key]);
      assert.notEqual(value.trim(), "", `${localeName}.${key} must be nonempty`);
      return value;
    });

    assert.equal(
      new Set(values).size,
      errorKeys.length,
      `${localeName} upload error copy must stay distinct`,
    );
    assert.notEqual(
      locale.serverUnsupportedFileType,
      locale.uploadFailed,
      `${localeName} unsupported-file copy must not collapse to uploadFailed`,
    );
    assert.notEqual(
      locale.uploadFailedRecoverable,
      locale.uploadFailed,
      `${localeName} recoverable copy must not collapse to uploadFailed`,
    );
  }
});
