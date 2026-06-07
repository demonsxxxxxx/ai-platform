import test from "node:test";
import assert from "node:assert/strict";
import * as XLSX from "xlsx";
import { worksheetToDisplayRows } from "../excelPreviewData.ts";

test("uses formatted worksheet text for date cells", () => {
  const worksheet = XLSX.utils.aoa_to_sheet([["month"], [46143.33383101852]]);
  worksheet.A2.z = "mmm-yy";

  const rows = worksheetToDisplayRows(worksheet, XLSX.utils);

  assert.deepEqual(rows, [["month"], ["May-26"]]);
});
