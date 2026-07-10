import assert from "node:assert/strict";
import test from "node:test";

import {
  EXCEL_PREVIEW_MAX_BYTES,
  EXCEL_PREVIEW_TIMEOUT_MS,
  parseExcelWorkbookPreview,
} from "../ExcelPreview.tsx";

type ZipMap = Record<string, string>;

function createZipLoader(entries: ZipMap) {
  return async () => ({
    file(path: string) {
      const contents = entries[path];
      if (contents == null) return null;
      return {
        async async(type: string) {
          assert.equal(type, "string");
          return contents;
        },
      };
    },
  });
}

test("parseExcelWorkbookPreview rejects oversized workbooks before unzip", async () => {
  await assert.rejects(
    () =>
      parseExcelWorkbookPreview(new ArrayBuffer(EXCEL_PREVIEW_MAX_BYTES + 1), {
        loadZip: createZipLoader({}),
      }),
    /excel_preview_file_too_large/,
  );
});

test("parseExcelWorkbookPreview rejects malformed workbooks without workbook metadata", async () => {
  await assert.rejects(
    () =>
      parseExcelWorkbookPreview(new ArrayBuffer(16), {
        loadZip: createZipLoader({}),
      }),
    /excel_preview_missing_workbook_xml/,
  );
});

test("parseExcelWorkbookPreview rejects unsupported non-OOXML workbook formats", async () => {
  await assert.rejects(
    () =>
      parseExcelWorkbookPreview(new ArrayBuffer(16), {
        fileName: "legacy.xls",
        loadZip: createZipLoader({}),
      }),
    /excel_preview_unsupported_format/,
  );
});

test("parseExcelWorkbookPreview rejects workbooks that exceed the parse time budget", async () => {
  let tick = 0;
  await assert.rejects(
    () =>
      parseExcelWorkbookPreview(new ArrayBuffer(16), {
        timeoutMs: EXCEL_PREVIEW_TIMEOUT_MS,
        now() {
          tick += EXCEL_PREVIEW_TIMEOUT_MS + 1;
          return tick;
        },
        loadZip: createZipLoader({
          "xl/workbook.xml":
            '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>',
          "xl/_rels/workbook.xml.rels":
            '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml" /></Relationships>',
          "xl/worksheets/sheet1.xml":
            '<?xml version="1.0" encoding="UTF-8"?><worksheet><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>hello</t></is></c></row></sheetData></worksheet>',
        }),
      }),
    /excel_preview_timeout/,
  );
});

test("parseExcelWorkbookPreview rejects oversized unpacked workbook entries", async () => {
  await assert.rejects(
    () =>
      parseExcelWorkbookPreview(new ArrayBuffer(32), {
        loadZip: async () => ({
          file(path: string) {
            if (path !== "xl/workbook.xml") {
              return null;
            }
            return {
              _data: {
                uncompressedSize: EXCEL_PREVIEW_MAX_BYTES,
              },
              async async(type: string) {
                assert.equal(type, "string");
                return '<?xml version="1.0" encoding="UTF-8"?><workbook />';
              },
            };
          },
        }),
      }),
    /excel_preview_entry_too_large/,
  );
});

test("parseExcelWorkbookPreview reads shared strings and inline text without xlsx", async () => {
  const sheets = await parseExcelWorkbookPreview(new ArrayBuffer(32), {
    loadZip: createZipLoader({
      "xl/workbook.xml":
        '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>',
      "xl/_rels/workbook.xml.rels":
        '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml" /></Relationships>',
      "xl/sharedStrings.xml":
        '<?xml version="1.0" encoding="UTF-8"?><sst><si><t>month</t></si><si><t>May-26</t></si></sst>',
      "xl/worksheets/sheet1.xml":
        '<?xml version="1.0" encoding="UTF-8"?><worksheet><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>May-26</t></is></c></row></sheetData></worksheet>',
    }),
  });

  assert.deepEqual(sheets, [
    {
      name: "Sheet1",
      data: [["month"], ["May-26"]],
    },
  ]);
});
