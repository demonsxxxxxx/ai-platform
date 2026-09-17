/* eslint-disable react-refresh/only-export-components */
import { memo, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";

import {
  getExcelGridExtent,
  resolveExcelImageRect,
  type ExcelImageRect,
  type ExcelPreviewImage,
} from "./excelImageLayout";

const excelPreviewStylesPromise =
  typeof document === "undefined"
    ? null
    : import("../../../styles/excel-preview.css");

if (excelPreviewStylesPromise) {
  void excelPreviewStylesPromise;
}

const FILE_PREVIEW_SCHEMA_VERSION = "ai-platform.file-preview.v2";
const MAX_XLSX_SHEETS = 16;
const MAX_XLSX_ROWS = 100;
const MAX_XLSX_COLUMNS = 32;
const MAX_XLSX_IMAGES = 16;
const MAX_XLSX_IMAGE_BYTES = 512 * 1024;
const MAX_XLSX_IMAGE_DATA_URL_CHARS = 700_000;
const MAX_XLSX_IMAGE_TOTAL_BYTES = 2 * 1024 * 1024;
const XLSX_PREVIEW_FAILURE_CODES = new Set([
  "xlsx_preview_encrypted_unsupported",
  "xlsx_preview_failed",
  "xlsx_preview_file_too_large",
  "xlsx_preview_limits_exceeded",
  "xlsx_preview_macros_unsupported",
  "xlsx_preview_timeout",
  "xlsx_preview_unavailable",
  "xlsx_preview_unsupported",
]);

type Translation = (key: string, options?: Record<string, unknown>) => string;
type CellValue = string | number | boolean;

export interface XlsxPreviewCell {
  column: number;
  kind: "boolean" | "datetime" | "number" | "text";
  value: CellValue;
}

export interface XlsxPreviewRow {
  row: number;
  cells: XlsxPreviewCell[];
}

export interface XlsxPreviewSheet {
  name: string;
  rows: XlsxPreviewRow[];
  images: ExcelPreviewImage[];
}

export interface XlsxPreviewDto {
  schema_version: typeof FILE_PREVIEW_SCHEMA_VERSION;
  kind: "xlsx_table";
  status: "ready" | "truncated" | "failed";
  content: { sheets: XlsxPreviewSheet[]; sheet_count: number } | null;
  truncated: boolean;
  warnings: string[];
  error: { code: string } | null;
}

interface ExcelPreviewProps {
  previewJson: string;
  t: Translation;
}

function useScrollIndicator(
  containerRef: React.RefObject<HTMLDivElement | null>,
) {
  const [progress, setProgress] = useState(0);
  const [hasOverflow, setHasOverflow] = useState(false);

  const update = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    const overflow = el.scrollWidth > el.clientWidth;
    setHasOverflow(overflow);
    if (overflow) {
      const max = el.scrollWidth - el.clientWidth;
      setProgress(max > 0 ? el.scrollLeft / max : 0);
    }
  }, [containerRef]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    update();
    el.addEventListener("scroll", update, { passive: true });
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => {
      el.removeEventListener("scroll", update);
      observer.disconnect();
    };
  }, [containerRef, update]);

  return { progress, hasOverflow };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function isPositiveInteger(value: unknown): value is number {
  return isNonNegativeInteger(value) && value >= 1;
}

function hasOnlyKeys(value: Record<string, unknown>, allowed: string[]): boolean {
  return Object.keys(value).every((key) => allowed.includes(key));
}

function parsePreviewCell(value: unknown): XlsxPreviewCell {
  if (
    !isRecord(value) ||
    !hasOnlyKeys(value, ["column", "kind", "value"]) ||
    !isPositiveInteger(value.column) ||
    value.column > MAX_XLSX_COLUMNS ||
    !["boolean", "datetime", "number", "text"].includes(
      String(value.kind),
    ) ||
    !["string", "number", "boolean"].includes(typeof value.value)
  ) {
    throw new Error("invalid_xlsx_preview_dto");
  }
  return {
    column: value.column,
    kind: value.kind as XlsxPreviewCell["kind"],
    value: value.value as CellValue,
  };
}

function parsePreviewRow(value: unknown): XlsxPreviewRow {
  if (
    !isRecord(value) ||
    !hasOnlyKeys(value, ["row", "cells"]) ||
    !isPositiveInteger(value.row) ||
    value.row > MAX_XLSX_ROWS ||
    !Array.isArray(value.cells) ||
    value.cells.length > MAX_XLSX_COLUMNS
  ) {
    throw new Error("invalid_xlsx_preview_dto");
  }
  return { row: value.row, cells: value.cells.map(parsePreviewCell) };
}

function parsePreviewAnchor(value: unknown): ExcelPreviewImage["anchor_from"] {
  if (
    !isRecord(value) ||
    !hasOnlyKeys(value, ["col", "row", "col_offset_emu", "row_offset_emu"]) ||
    !isNonNegativeInteger(value.col) ||
    value.col >= MAX_XLSX_COLUMNS ||
    !isNonNegativeInteger(value.row) ||
    value.row >= MAX_XLSX_ROWS ||
    !isNonNegativeInteger(value.col_offset_emu) ||
    value.col_offset_emu > 95_250_000 ||
    !isNonNegativeInteger(value.row_offset_emu) ||
    value.row_offset_emu > 95_250_000
  ) {
    throw new Error("invalid_xlsx_preview_dto");
  }
  return {
    col: value.col,
    row: value.row,
    col_offset_emu: value.col_offset_emu,
    row_offset_emu: value.row_offset_emu,
  };
}

function parsePreviewImageExtent(value: unknown): NonNullable<ExcelPreviewImage["extent"]> {
  if (
    !isRecord(value) ||
    !hasOnlyKeys(value, ["width_emu", "height_emu"]) ||
    !isPositiveInteger(value.width_emu) ||
    value.width_emu > 95_250_000 ||
    !isPositiveInteger(value.height_emu) ||
    value.height_emu > 95_250_000
  ) {
    throw new Error("invalid_xlsx_preview_dto");
  }
  return { width_emu: value.width_emu, height_emu: value.height_emu };
}

function decodeBase64ByteLength(encoded: string): number {
  try {
    return atob(encoded).length;
  } catch {
    throw new Error("invalid_xlsx_preview_dto");
  }
}

function parsePreviewImage(value: unknown): ExcelPreviewImage {
  if (
    !isRecord(value) ||
    !hasOnlyKeys(value, [
      "id",
      "name",
      "description",
      "mime_type",
      "data_url",
      "anchor_from",
      "anchor_to",
      "extent",
      "order",
    ]) ||
    typeof value.id !== "string" ||
    !value.id ||
    value.id.length > 128 ||
    typeof value.name !== "string" ||
    !value.name ||
    value.name.length > 256 ||
    typeof value.description !== "string" ||
    value.description.length > 256 ||
    !["image/bmp", "image/gif", "image/jpeg", "image/png", "image/webp"].includes(
      String(value.mime_type),
    ) ||
    typeof value.data_url !== "string" ||
    !isNonNegativeInteger(value.order) ||
    value.order >= MAX_XLSX_IMAGES
  ) {
    throw new Error("invalid_xlsx_preview_dto");
  }

  const mimeType = value.mime_type as ExcelPreviewImage["mime_type"];
  const prefix = `data:${mimeType};base64,`;
  if (!value.data_url.startsWith(prefix)) {
    throw new Error("invalid_xlsx_preview_dto");
  }
  const encoded = value.data_url.slice(prefix.length);
  if (
    !encoded ||
    encoded.length > MAX_XLSX_IMAGE_DATA_URL_CHARS ||
    encoded.length % 4 === 1 ||
    !/^[A-Za-z0-9+/]*={0,2}$/.test(encoded)
  ) {
    throw new Error("invalid_xlsx_preview_dto");
  }
  const decodedByteLength = decodeBase64ByteLength(encoded);
  if (!decodedByteLength || decodedByteLength > MAX_XLSX_IMAGE_BYTES) {
    throw new Error("invalid_xlsx_preview_dto");
  }

  const anchorFrom = parsePreviewAnchor(value.anchor_from);
  const anchorTo = value.anchor_to === null ? null : parsePreviewAnchor(value.anchor_to);
  const extent = value.extent === null ? null : parsePreviewImageExtent(value.extent);
  if ((anchorTo === null) === (extent === null)) {
    throw new Error("invalid_xlsx_preview_dto");
  }
  if (
    anchorTo &&
    (anchorTo.row < anchorFrom.row ||
      anchorTo.col < anchorFrom.col ||
      (anchorTo.row === anchorFrom.row &&
        anchorTo.row_offset_emu <= anchorFrom.row_offset_emu) ||
      (anchorTo.col === anchorFrom.col &&
        anchorTo.col_offset_emu <= anchorFrom.col_offset_emu))
  ) {
    throw new Error("invalid_xlsx_preview_dto");
  }

  return {
    id: value.id,
    name: value.name,
    description: value.description,
    mime_type: mimeType,
    data_url: value.data_url,
    anchor_from: anchorFrom,
    anchor_to: anchorTo,
    extent,
    order: value.order,
  };
}

function parsePreviewSheet(value: unknown): XlsxPreviewSheet {
  if (
    !isRecord(value) ||
    !hasOnlyKeys(value, ["name", "rows", "images"]) ||
    typeof value.name !== "string" ||
    !value.name ||
    value.name.length > 256 ||
    !Array.isArray(value.rows) ||
    value.rows.length > MAX_XLSX_ROWS ||
    !Array.isArray(value.images) ||
    value.images.length > MAX_XLSX_IMAGES
  ) {
    throw new Error("invalid_xlsx_preview_dto");
  }
  return {
    name: value.name,
    rows: value.rows.map(parsePreviewRow),
    images: value.images.map(parsePreviewImage),
  };
}

/** Validate the server-owned presentation DTO; no workbook bytes are parsed here. */
export function parseXlsxPreviewDto(payload: string): XlsxPreviewDto {
  let value: unknown;
  try {
    value = JSON.parse(payload);
  } catch {
    throw new Error("invalid_xlsx_preview_dto");
  }
  if (
    !isRecord(value) ||
    !hasOnlyKeys(value, [
      "schema_version",
      "kind",
      "status",
      "content",
      "truncated",
      "warnings",
      "error",
    ]) ||
    value.schema_version !== FILE_PREVIEW_SCHEMA_VERSION ||
    value.kind !== "xlsx_table" ||
    !["ready", "truncated", "failed"].includes(String(value.status)) ||
    typeof value.truncated !== "boolean" ||
    !Array.isArray(value.warnings) ||
    !value.warnings.every((warning) => typeof warning === "string")
  ) {
    throw new Error("invalid_xlsx_preview_dto");
  }

  if (value.status === "failed") {
    if (
      value.content !== null ||
      value.truncated ||
      !isRecord(value.error) ||
      !hasOnlyKeys(value.error, ["code"]) ||
      typeof value.error.code !== "string" ||
      !XLSX_PREVIEW_FAILURE_CODES.has(value.error.code)
    ) {
      throw new Error("invalid_xlsx_preview_dto");
    }
    return {
      schema_version: FILE_PREVIEW_SCHEMA_VERSION,
      kind: "xlsx_table",
      status: "failed",
      content: null,
      truncated: false,
      warnings: value.warnings as string[],
      error: { code: value.error.code },
    };
  }

  if (
    !isRecord(value.content) ||
    !hasOnlyKeys(value.content, ["sheets", "sheet_count"]) ||
    !Array.isArray(value.content.sheets) ||
    value.content.sheets.length > MAX_XLSX_SHEETS ||
    !isNonNegativeInteger(value.content.sheet_count) ||
    value.content.sheet_count < value.content.sheets.length ||
    value.error !== null ||
    (value.status === "ready" && value.truncated) ||
    (value.status === "truncated" && !value.truncated)
  ) {
    throw new Error("invalid_xlsx_preview_dto");
  }
  const sheets = value.content.sheets.map(parsePreviewSheet);
  const images = sheets.flatMap((sheet) => sheet.images);
  if (
    images.length > MAX_XLSX_IMAGES ||
    images.reduce((total, image) => {
      const encoded = image.data_url.slice(image.data_url.indexOf(",") + 1);
      return total + decodeBase64ByteLength(encoded);
    }, 0) > MAX_XLSX_IMAGE_TOTAL_BYTES
  ) {
    throw new Error("invalid_xlsx_preview_dto");
  }
  return {
    ...(value as Omit<XlsxPreviewDto, "content">),
    content: {
      sheet_count: value.content.sheet_count,
      sheets,
    },
  };
}

function colLabel(index: number): string {
  let label = "";
  let n = index;
  while (n >= 0) {
    label = String.fromCharCode(65 + (n % 26)) + label;
    n = Math.floor(n / 26) - 1;
  }
  return label;
}

function isNumeric(v: unknown): boolean {
  if (v == null || v === "") return false;
  return !Number.isNaN(Number(v));
}

function mapExcelPreviewError(error: unknown, t: Translation): string {
  const code = error instanceof Error ? error.message : "";
  if (code === "invalid_xlsx_preview_dto") {
    return t("documents.excelPreviewInvalidResponse", {
      defaultValue: "Workbook preview returned an invalid response. Download the original file to inspect it.",
    });
  }
  switch (code) {
    case "xlsx_preview_file_too_large":
    case "xlsx_preview_limits_exceeded":
      return t("documents.excelPreviewLimitsExceeded", {
        defaultValue:
          "Workbook preview is unavailable because the sheet exceeds the server safety limits.",
      });
    case "xlsx_preview_timeout":
    case "xlsx_preview_unavailable":
      return t("documents.excelPreviewTimeout", {
        defaultValue:
          "Workbook preview is temporarily unavailable. Download the original file to inspect it.",
      });
    case "xlsx_preview_encrypted_unsupported":
      return t("documents.excelPreviewEncrypted", {
        defaultValue:
          "Encrypted workbooks are not available for preview. Download the original file to inspect it.",
      });
    case "xlsx_preview_macros_unsupported":
      return t("documents.excelPreviewMacrosUnsupported", {
        defaultValue:
          "Macro-enabled workbooks are not available for preview. Download the original file to inspect it.",
      });
    case "xlsx_preview_unsupported":
      return t("documents.excelPreviewUnsupportedFormat", {
        defaultValue:
          "This workbook format is not available for preview. Download the original file to inspect it.",
      });
    default:
      return t("documents.excelParseError", {
        defaultValue:
          "Workbook preview could not be produced safely. Download the original file to inspect it.",
      });
  }
}

const ExcelPreview = memo(function ExcelPreview({
  previewJson,
  t,
}: ExcelPreviewProps) {
  const [activeSheet, setActiveSheet] = useState(0);
  const [hoveredCell, setHoveredCell] = useState<{
    row: number;
    col: number;
  } | null>(null);
  const previewInstanceId = useId();
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const gridSurfaceRef = useRef<HTMLDivElement>(null);
  const sheetTabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const [imageRects, setImageRects] = useState<Map<string, ExcelImageRect>>(
    () => new Map(),
  );
  const { progress, hasOverflow } = useScrollIndicator(scrollContainerRef);

  const parsedPreview = useMemo(() => {
    try {
      return { preview: parseXlsxPreviewDto(previewJson), error: null };
    } catch (error) {
      return { preview: null, error: mapExcelPreviewError(error, t) };
    }
  }, [previewJson, t]);
  const preview = parsedPreview.preview;
  const error =
    parsedPreview.error ??
    (preview?.status === "failed"
      ? mapExcelPreviewError(new Error(preview.error?.code), t)
      : null);

  const sheets = preview?.content?.sheets ?? [];
  const currentSheet = sheets[activeSheet];
  const tabPanelId = `${previewInstanceId}-xlsx-preview-table`;

  const gridExtent = useMemo(() => {
    if (!currentSheet) return { rows: 0, cols: 0 };
    return getExcelGridExtent(currentSheet.rows, currentSheet.images);
  }, [currentSheet]);
  const totalRows = gridExtent.rows;
  const totalCols = gridExtent.cols;
  const headerRow = currentSheet?.rows.find((row) => row.row === 1);
  const dataRows = currentSheet?.rows.filter((row) => row.row > 1) ?? [];

  const getCellValue = useCallback(
    (rowIndex: number, colIndex: number): string => {
      const row = currentSheet?.rows.find((candidate) => candidate.row === rowIndex + 1);
      const cell = row?.cells.find(
        (candidate) => candidate.column === colIndex + 1,
      );
      return cell == null ? "" : String(cell.value);
    },
    [currentSheet],
  );

  const getSheetRowNumber = useCallback(
    (rowIndex: number): number => rowIndex + 1,
    [],
  );

  useLayoutEffect(() => {
    const surface = gridSurfaceRef.current;
    if (!surface || !currentSheet || currentSheet.images.length === 0) {
      setImageRects(new Map());
      return;
    }

    const update = () => {
      const columnStarts = Array.from({ length: totalCols }, (_, index) => {
        const element = surface.querySelector<HTMLElement>(
          `[data-excel-column-index="${index}"]`,
        );
        return element?.offsetLeft ?? 0;
      });
      const rowStarts = Array.from({ length: totalRows }, (_, index) => {
        const element = surface.querySelector<HTMLElement>(
          `[data-excel-row-index="${index}"]`,
        );
        return element?.offsetTop ?? 0;
      });
      const metrics = { columnStarts, rowStarts };
      const next = new Map<string, ExcelImageRect>();
      for (const image of currentSheet.images) {
        const rect = resolveExcelImageRect(image, metrics);
        if (rect) next.set(image.id, rect);
      }
      setImageRects(next);
    };

    update();
    const observer = new ResizeObserver(update);
    observer.observe(surface);
    return () => observer.disconnect();
  }, [currentSheet, totalCols, totalRows]);

  const handleCellHover = useCallback((rowIndex: number, colIndex: number) => {
    setHoveredCell({ row: rowIndex, col: colIndex });
  }, []);

  const handleCellLeave = useCallback(() => {
    setHoveredCell(null);
  }, []);

  const handleSheetKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLButtonElement>) => {
      if (sheets.length === 0) return;
      let nextIndex: number | null = null;
      if (event.key === "ArrowLeft") nextIndex = Math.max(0, activeSheet - 1);
      if (event.key === "ArrowRight") {
        nextIndex = Math.min(sheets.length - 1, activeSheet + 1);
      }
      if (event.key === "Home") nextIndex = 0;
      if (event.key === "End") nextIndex = sheets.length - 1;
      if (nextIndex !== null) {
        event.preventDefault();
        setActiveSheet(nextIndex);
        sheetTabRefs.current[nextIndex]?.focus();
      }
    },
    [activeSheet, sheets.length],
  );

  if (error) {
    return (
      <div role="alert" className="p-4 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800">
        <p className="text-sm text-red-600 dark:text-red-400 font-medium">
          {t("documents.excelPreviewError")}: {error}
        </p>
      </div>
    );
  }

  const displayRows = totalRows;

  return (
    <div className="flex flex-col h-full bg-white dark:bg-stone-950">
      <div className="flex items-center gap-2 px-3 py-1.5 bg-stone-50 dark:bg-stone-900 border-b border-stone-200 dark:border-stone-700 shrink-0">
        <span className="text-[11px] font-bold text-stone-600 dark:text-stone-400 w-8 text-center shrink-0 italic">
          fx
        </span>
        <span className="text-[12px] text-stone-500 dark:text-stone-400 truncate font-mono min-w-[3rem]">
          {hoveredCell
            ? `${colLabel(hoveredCell.col)}${getSheetRowNumber(hoveredCell.row)}`
            : "A1"}
        </span>
        <span className="h-4 w-px bg-stone-300 dark:bg-stone-600 shrink-0" />
        <span className="text-[12px] text-stone-700 dark:text-stone-300 truncate">
          {hoveredCell
            ? getCellValue(hoveredCell.row, hoveredCell.col)
            : headerRow
              ? getCellValue(0, 0)
              : ""}
        </span>
      </div>

      <div className="flex items-center gap-0.5 px-1 py-0 bg-stone-100 dark:bg-stone-900 border-b border-stone-300 dark:border-stone-700 shrink-0">
        <button
          type="button"
          onClick={() => setActiveSheet((current) => Math.max(0, current - 1))}
          disabled={activeSheet === 0}
          aria-label={t("documents.excelPreviousSheet", { defaultValue: "Previous sheet" })}
          className="p-1 min-w-11 min-h-11 text-stone-400 hover:text-stone-600 dark:hover:text-stone-300 disabled:opacity-30 disabled:cursor-default transition-colors"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="15 18 9 12 15 6" />
          </svg>
        </button>
        <div role="tablist" aria-label={t("documents.excelSheets", { defaultValue: "Workbook sheets" })} className="flex-1 flex items-center gap-0.5 overflow-x-auto px-1 py-1">
          {sheets.map((sheet, index) => (
            <button
              key={`${index}:${sheet.name}`}
              id={`${previewInstanceId}-xlsx-preview-tab-${index}`}
              role="tab"
              aria-controls={tabPanelId}
              aria-selected={activeSheet === index}
              tabIndex={activeSheet === index ? 0 : -1}
              ref={(element) => {
                sheetTabRefs.current[index] = element;
              }}
              onClick={() => setActiveSheet(index)}
              onKeyDown={handleSheetKeyDown}
              className={`px-3 py-0.5 text-[11px] font-medium rounded-sm whitespace-nowrap transition-all ${
                activeSheet === index
                  ? "bg-white dark:bg-stone-800 text-stone-800 dark:text-stone-100 shadow-sm border border-stone-300 dark:border-stone-600"
                  : "text-stone-500 dark:text-stone-400 hover:text-stone-700 dark:hover:text-stone-200 hover:bg-stone-200/50 dark:hover:bg-stone-800/50"
              }`}
            >
              {sheet.name}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={() =>
            setActiveSheet((current) => Math.min(sheets.length - 1, current + 1))
          }
          disabled={activeSheet === sheets.length - 1}
          aria-label={t("documents.excelNextSheet", { defaultValue: "Next sheet" })}
          className="p-1 min-w-11 min-h-11 text-stone-400 hover:text-stone-600 dark:hover:text-stone-300 disabled:opacity-30 disabled:cursor-default transition-colors"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="9 6 15 12 9 18" />
          </svg>
        </button>
      </div>

      {preview?.status === "truncated" && (
        <p className="px-3 py-1.5 text-xs text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-950/30 border-b border-amber-200 dark:border-amber-900">
          {t("documents.excelPreviewTruncated", {
            defaultValue: "Showing a bounded table preview; download the original workbook for complete data.",
          })}
        </p>
      )}

      <div id={tabPanelId} role="tabpanel" aria-labelledby={`${previewInstanceId}-xlsx-preview-tab-${activeSheet}`} ref={scrollContainerRef} className="flex-1 overflow-auto relative overscroll-x-contain [-webkit-overflow-scrolling:touch] excel-preview-scroll border-x border-stone-300 dark:border-stone-600">
        <div ref={gridSurfaceRef} className="relative w-max min-w-full">
          <table className="border-collapse w-max min-w-full text-[13px]">
          <thead>
            <tr className="sticky top-0 z-10">
              <th className="sticky left-0 z-20 w-8 sm:w-10 min-w-[2rem] sm:min-w-[2.5rem] max-w-[2rem] sm:max-w-[2.5rem] px-0 py-0 text-center text-[11px] text-stone-500 dark:text-stone-400 bg-stone-100 dark:bg-stone-800 border-r border-b border-stone-300 dark:border-stone-600 select-none" />
              {Array.from({ length: totalCols }, (_, index) => (
                <th
                  key={index}
                  data-excel-column-index={index}
                  className={`min-w-[60px] sm:min-w-[80px] h-6 px-0 py-0 text-center text-[11px] font-normal text-stone-500 dark:text-stone-400 bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-600 select-none leading-6 ${hoveredCell?.col === index ? "bg-stone-100 dark:bg-stone-800 text-stone-700 dark:text-stone-300" : ""}`}
                >
                  {colLabel(index)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: displayRows }, (_, rawRowIndex) => {
              const isHeader = rawRowIndex === 0;
              const isRowHovered = hoveredCell && !isHeader && hoveredCell.row === rawRowIndex;
              return (
                <tr
                  key={rawRowIndex}
                  data-excel-row-index={rawRowIndex}
                >
                  <td className={`sticky left-0 z-10 w-8 sm:w-10 min-w-[2rem] sm:min-w-[2.5rem] max-w-[2rem] sm:max-w-[2.5rem] px-0 py-0 text-center text-[11px] bg-stone-100 dark:bg-stone-800 border-r border-b border-stone-300 dark:border-stone-600 select-none tabular-nums leading-6 touch-none [box-shadow:2px_0_4px_-1px_rgba(0,0,0,0.06)] dark:[box-shadow:2px_0_4px_-1px_rgba(0,0,0,0.3)] ${isHeader ? "text-stone-400 dark:text-stone-500" : isRowHovered ? "text-blue-700 dark:text-blue-300 bg-blue-50 dark:bg-blue-900/40" : "text-stone-500 dark:text-stone-400"}`}>
                    {isHeader ? "" : getSheetRowNumber(rawRowIndex)}
                  </td>
                  {Array.from({ length: totalCols }, (_, colIndex) => {
                    const value = getCellValue(rawRowIndex, colIndex);
                    const isCellHovered = hoveredCell && !isHeader && hoveredCell.row === rawRowIndex && hoveredCell.col === colIndex;
                    if (isHeader) {
                      return (
                        <th key={colIndex} onMouseEnter={() => handleCellHover(rawRowIndex, colIndex)} onMouseLeave={handleCellLeave} className={`min-h-[24px] min-w-[60px] sm:min-w-[80px] px-2 py-0 text-[13px] leading-6 border border-stone-300 dark:border-stone-600 whitespace-nowrap text-left font-semibold text-stone-700 dark:text-stone-300 bg-stone-50 dark:bg-stone-800/60 ${isCellHovered ? "!outline outline-2 outline-stone-500 dark:outline-stone-400 outline-offset-[-1px] bg-stone-100/60 dark:bg-stone-800/40 !border-stone-400 dark:!border-stone-500" : ""}`}>
                          {value || " "}
                        </th>
                      );
                    }
                    return (
                      <td key={colIndex} onMouseEnter={() => handleCellHover(rawRowIndex, colIndex)} onMouseLeave={handleCellLeave} className={`min-h-[24px] min-w-[60px] sm:min-w-[80px] px-2 py-0 text-[13px] leading-6 border border-stone-200 dark:border-stone-700/80 whitespace-nowrap text-stone-800 dark:text-stone-200 ${isNumeric(value) ? "text-right tabular-nums font-mono" : "text-left"} ${isCellHovered ? "!outline outline-2 outline-stone-500 dark:outline-stone-400 outline-offset-[-1px] bg-stone-100/60 dark:bg-stone-800/40 !border-stone-400 dark:!border-stone-500" : isRowHovered ? "bg-stone-50/70 dark:bg-stone-800/30" : ""}`}>
                        {value || " "}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
          </table>

          <div
            className="absolute inset-0 z-[5] pointer-events-none"
            aria-label={t("documents.excelImages", { defaultValue: "Worksheet images" })}
          >
            {currentSheet?.images.map((image) => {
              const rect = imageRects.get(image.id);
              if (!rect) return null;
              return (
                <img
                  key={image.id}
                  data-excel-embedded-image
                  src={image.data_url}
                  alt={image.description || image.name}
                  className="absolute max-w-none select-none"
                  style={{
                    left: rect.left,
                    top: rect.top,
                    width: rect.width,
                    height: rect.height,
                    objectFit: "fill",
                    zIndex: image.order,
                  }}
                  draggable={false}
                />
              );
            })}
          </div>
        </div>

        {displayRows === 0 && (
          <div className="flex flex-col items-center justify-center py-20 text-stone-400 dark:text-stone-500">
            <p className="text-sm">{t("documents.noData") || "No data"}</p>
          </div>
        )}

        {hasOverflow && (
          <div className="absolute bottom-0 left-0 right-0 h-1 z-30 pointer-events-none">
            <div className="h-full bg-stone-300/40 dark:bg-stone-600/40" />
            <div className="absolute top-0 h-full bg-stone-400 dark:bg-stone-500 transition-[left] duration-75" style={{ width: `${Math.max(10, (1 - progress) * 100)}%`, left: `${progress * 100}%` }} />
          </div>
        )}
      </div>

      <div className="flex items-center justify-between gap-3 px-3 py-1 text-[11px] text-stone-500 dark:text-stone-400 bg-stone-100 dark:bg-stone-800 border-t border-stone-300 dark:border-stone-600 shrink-0">
        <span className="tabular-nums">
          {sheets.length > 1 && <span className="mr-2 text-stone-400 dark:text-stone-500">{currentSheet?.name}</span>}
          {t("documents.excelRowsAndCols", { rows: dataRows.length, cols: totalCols })}
        </span>
        <span className="text-right">
          {t("documents.excelPreviewLimitations", {
            defaultValue: "Styles and charts are not reproduced; formulas are omitted; macros and external links are not run.",
          })}
        </span>
      </div>
    </div>
  );
});

export default ExcelPreview;
