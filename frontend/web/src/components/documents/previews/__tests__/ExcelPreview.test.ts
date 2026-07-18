import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { parseXlsxPreviewDto } from "../ExcelPreview.tsx";

const sourceSha256 = "a".repeat(64);

function dto(overrides: Record<string, unknown> = {}) {
  return JSON.stringify({
    schema_version: "ai-platform.file-preview.v1",
    kind: "xlsx_table",
    status: "ready",
    source_sha256: sourceSha256,
    parser_id: "ai-platform.xlsx.openpyxl",
    parser_version: "1",
    content: {
      sheet_count: 1,
      sheets: [
        {
          name: "Checks",
          rows: [
            {
              row: 1,
              cells: [
                { column: 1, kind: "text", value: "requirement" },
                { column: 2, kind: "boolean", value: true },
              ],
            },
            {
              row: 3,
              cells: [{ column: 2, kind: "number", value: 42 }],
            },
          ],
        },
      ],
    },
    truncated: false,
    warnings: ["styles_not_rendered"],
    error: null,
    ...overrides,
  });
}

test("accepts the versioned sparse table DTO emitted by the server", () => {
  const preview = parseXlsxPreviewDto(dto());

  assert.equal(preview.status, "ready");
  assert.equal(preview.content?.sheets[0].name, "Checks");
  assert.deepEqual(preview.content?.sheets[0].rows[1], {
    row: 3,
    cells: [{ column: 2, kind: "number", value: 42 }],
  });
});

test("accepts explicit truncation but rejects inconsistent status payloads", () => {
  const truncated = parseXlsxPreviewDto(
    dto({ status: "truncated", truncated: true }),
  );
  assert.equal(truncated.status, "truncated");

  assert.throws(
    () => parseXlsxPreviewDto(dto({ status: "ready", truncated: true })),
    /invalid_xlsx_preview_dto/,
  );
});

test("accepts stable public failures without rendering workbook content", () => {
  const failed = parseXlsxPreviewDto(
    dto({
      status: "failed",
      content: null,
      truncated: false,
      warnings: [],
      error: { code: "xlsx_preview_timeout" },
    }),
  );

  assert.equal(failed.error?.code, "xlsx_preview_timeout");
  assert.equal(failed.content, null);
});

test("fails closed for malformed or unexpected preview responses", () => {
  assert.throws(
    () => parseXlsxPreviewDto("not-json"),
    /invalid_xlsx_preview_dto/,
  );
  assert.throws(
    () => parseXlsxPreviewDto(dto({ storage_key: "private/secret.xlsx" })),
    /invalid_xlsx_preview_dto/,
  );
  assert.throws(
    () =>
      parseXlsxPreviewDto(
        dto({
          status: "failed",
          content: null,
          truncated: false,
          error: { code: "untrusted_parser_stack" },
        }),
      ),
    /invalid_xlsx_preview_dto/,
  );
});

test("contains no browser ZIP or XML parser implementation", () => {
  const source = readFileSync(new URL("../ExcelPreview.tsx", import.meta.url), "utf8");

  assert.doesNotMatch(source, /from "jszip"/);
  assert.doesNotMatch(source, /from "saxes"/);
  assert.doesNotMatch(source, /parseExcelWorkbookPreview/);
});

test("loads XLSX previews as authenticated DTO JSON and passes no workbook bytes to the renderer", () => {
  const stateSource = readFileSync(
    new URL("../../useDocumentPreviewState.ts", import.meta.url),
    "utf8",
  );
  const contentSource = readFileSync(
    new URL("../../DocumentPreviewContent.tsx", import.meta.url),
    "utf8",
  );

  assert.match(
    stateSource,
    /else if \(excelFile\) \{\s+const previewJson = await fetchDocumentText\(url\);/,
  );
  assert.doesNotMatch(stateSource, /wordPreviewFile \|\| excelFile/);
  assert.match(contentSource, /<ExcelPreview previewJson=\{data\.content\} t=\{t\}/);
  assert.doesNotMatch(contentSource, /<ExcelPreview arrayBuffer=/);
});
