import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import { readFileSync } from "node:fs";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import ExcelPreview, { parseXlsxPreviewDto } from "../ExcelPreview.tsx";

function dto(overrides: Record<string, unknown> = {}) {
  return JSON.stringify({
    schema_version: "ai-platform.file-preview.v2",
    kind: "xlsx_table",
    status: "ready",
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
          images: [],
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

test("accepts only server-owned image data and preserves its worksheet geometry", () => {
  const preview = parseXlsxPreviewDto(
    dto({
      content: {
        sheet_count: 1,
        sheets: [
          {
            name: "Checks",
            rows: [],
            images: [
              {
                id: "sheet-0-image-0",
                name: "Logo",
                description: "",
                mime_type: "image/png",
                data_url: "data:image/png;base64,iVBORw0KGgo=",
                anchor_from: {
                  col: 1,
                  row: 2,
                  col_offset_emu: 0,
                  row_offset_emu: 0,
                },
                anchor_to: null,
                extent: { width_emu: 914400, height_emu: 457200 },
                order: 0,
              },
            ],
          },
        ],
      },
    }),
  );

  assert.equal(preview.content?.sheets[0].images[0].name, "Logo");
  assert.throws(
    () =>
      parseXlsxPreviewDto(
        dto({
          content: {
            sheet_count: 1,
            sheets: [
              {
                name: "Checks",
                rows: [],
                images: [
                  {
                    id: "unsafe",
                    name: "Unsafe",
                    description: "",
                    mime_type: "image/png",
                    data_url: "https://example.com/image.png",
                    anchor_from: {
                      col: 0,
                      row: 0,
                      col_offset_emu: 0,
                      row_offset_emu: 0,
                    },
                    anchor_to: null,
                    extent: { width_emu: 914400, height_emu: 457200 },
                    order: 0,
                  },
                ],
              },
            ],
          },
        }),
      ),
    /invalid_xlsx_preview_dto/,
  );
});

test("accepts the exact server image byte ceiling across multiple images", () => {
  const encoded = Buffer.alloc(512 * 1024).toString("base64");
  const preview = parseXlsxPreviewDto(
    dto({
      content: {
        sheet_count: 1,
        sheets: [
          {
            name: "Checks",
            rows: [],
            images: Array.from({ length: 4 }, (_, order) => ({
              id: `sheet-0-image-${order}`,
              name: `Image ${order}`,
              description: "",
              mime_type: "image/png",
              data_url: `data:image/png;base64,${encoded}`,
              anchor_from: {
                col: order,
                row: 0,
                col_offset_emu: 0,
                row_offset_emu: 0,
              },
              anchor_to: null,
              extent: { width_emu: 914400, height_emu: 457200 },
              order,
            })),
          },
        ],
      },
    }),
  );

  assert.equal(preview.content?.sheets[0].images.length, 4);
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
    () => parseXlsxPreviewDto(dto({ source_sha256: "a".repeat(64) })),
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
  assert.throws(
    () =>
      parseXlsxPreviewDto(
        dto({
          content: {
            sheet_count: 1,
            sheets: [
              {
                name: "Checks",
                rows: [
                  {
                    row: 1,
                    cells: [
                      {
                        column: 1,
                        kind: "formula",
                        value: "=SUM(40,2)",
                      },
                    ],
                  },
                ],
                images: [],
              },
            ],
          },
        }),
      ),
    /invalid_xlsx_preview_dto/,
  );
  assert.throws(
    () =>
      parseXlsxPreviewDto(
        dto({
          content: {
            sheet_count: 1,
            sheets: [
              {
                name: "Checks",
                rows: [{ row: 101, cells: [] }],
                images: [],
              },
            ],
          },
        }),
      ),
    /invalid_xlsx_preview_dto/,
  );
});

test("renders real tab semantics and labelled sheet controls", () => {
  const markup = renderToStaticMarkup(
    createElement(ExcelPreview, {
      previewJson: dto(),
      t: (_key, options) => String(options?.defaultValue ?? "translated"),
    }),
  );

  assert.match(markup, /role="tablist"/);
  assert.match(markup, /role="tab"/);
  assert.match(markup, /aria-selected="true"/);
  assert.match(markup, /aria-label="Previous sheet"/);
  assert.match(markup, /aria-label="Next sheet"/);
  assert.match(markup, /role="tabpanel"/);
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
    /else if \(xlsxPreviewFile\) \{\s+const previewJson = await fetchXlsxPreviewJson\(url\);/,
  );
  assert.match(stateSource, /setData\(null\);/);
  assert.match(stateSource, /const currentData = isCurrentData \? data : null;/);
  assert.match(contentSource, /<ExcelPreview key=\{previewIdentity\} previewJson=\{data\.content\} t=\{t\}/);
  assert.doesNotMatch(contentSource, /<ExcelPreview arrayBuffer=/);
});
