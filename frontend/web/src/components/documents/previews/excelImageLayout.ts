export interface ExcelPreviewAnchor {
  col: number;
  row: number;
  col_offset_emu: number;
  row_offset_emu: number;
}

export interface ExcelPreviewImage {
  id: string;
  name: string;
  description: string;
  mime_type: "image/bmp" | "image/gif" | "image/jpeg" | "image/png" | "image/webp";
  data_url: string;
  anchor_from: ExcelPreviewAnchor;
  anchor_to: ExcelPreviewAnchor | null;
  extent: { width_emu: number; height_emu: number } | null;
  order: number;
}

const EMU_PER_CSS_PIXEL = 9525;

export interface ExcelGridMetrics {
  columnStarts: number[];
  rowStarts: number[];
}

export interface ExcelImageRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

export function getExcelGridExtent(
  rows: readonly { row: number; cells: readonly { column: number }[] }[],
  images: readonly ExcelPreviewImage[],
): { rows: number; cols: number } {
  let rowCount = rows.reduce((maximum, row) => Math.max(maximum, row.row), 0);
  let colCount = rows.reduce(
    (maximum, row) =>
      Math.max(maximum, ...row.cells.map((cell) => cell.column)),
    0,
  );

  for (const image of images) {
    rowCount = Math.max(
      rowCount,
      image.anchor_from.row + 1,
      (image.anchor_to?.row ?? -1) + 1,
    );
    colCount = Math.max(
      colCount,
      image.anchor_from.col + 1,
      (image.anchor_to?.col ?? -1) + 1,
    );
  }

  return { rows: rowCount, cols: colCount };
}

function pointToPixels(
  point: ExcelPreviewAnchor,
  metrics: ExcelGridMetrics,
): { left: number; top: number } | null {
  const left = metrics.columnStarts[point.col];
  const top = metrics.rowStarts[point.row];
  if (left == null || top == null) return null;
  return {
    left: left + point.col_offset_emu / EMU_PER_CSS_PIXEL,
    top: top + point.row_offset_emu / EMU_PER_CSS_PIXEL,
  };
}

export function resolveExcelImageRect(
  image: ExcelPreviewImage,
  metrics: ExcelGridMetrics,
): ExcelImageRect | null {
  const start = pointToPixels(image.anchor_from, metrics);
  if (!start) return null;

  const end = image.anchor_to ? pointToPixels(image.anchor_to, metrics) : null;
  const width = end
    ? end.left - start.left
    : (image.extent?.width_emu ?? 0) / EMU_PER_CSS_PIXEL;
  const height = end
    ? end.top - start.top
    : (image.extent?.height_emu ?? 0) / EMU_PER_CSS_PIXEL;
  if (width <= 0 || height <= 0) return null;

  return { left: start.left, top: start.top, width, height };
}
