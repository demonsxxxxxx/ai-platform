import assert from "node:assert/strict";
import test from "node:test";

import {
  getExcelGridExtent,
  resolveExcelImageRect,
} from "../excelImageLayout.ts";

const image = {
  id: "sheet-0-image-0",
  name: "Logo",
  description: "",
  mime_type: "image/png" as const,
  data_url: "data:image/png;base64,iVBORw0KGgo=",
  anchor_from: { col: 1, row: 2, col_offset_emu: 9525, row_offset_emu: 19050 },
  anchor_to: null,
  extent: { width_emu: 914400, height_emu: 457200 },
  order: 0,
};

test("extends a sparse grid through embedded image geometry", () => {
  assert.deepEqual(
    getExcelGridExtent(
      [{ row: 1, cells: [{ column: 1 }] }],
      [{ ...image, anchor_to: { ...image.anchor_from, col: 5, row: 8 }, extent: null }],
    ),
    { rows: 9, cols: 6 },
  );
});

test("maps EMU offsets and one-cell extents to CSS pixels", () => {
  assert.deepEqual(
    resolveExcelImageRect(image, {
      columnStarts: [40, 120, 200],
      rowStarts: [24, 48, 72, 96],
    }),
    { left: 121, top: 74, width: 96, height: 48 },
  );
});

test("uses a two-cell endpoint when it is present", () => {
  assert.deepEqual(
    resolveExcelImageRect(
      {
        ...image,
        anchor_to: {
          col: 2,
          row: 3,
          col_offset_emu: 19050,
          row_offset_emu: 9525,
        },
        extent: null,
      },
      { columnStarts: [40, 120, 200], rowStarts: [24, 48, 72, 96] },
    ),
    { left: 121, top: 74, width: 81, height: 23 },
  );
});
