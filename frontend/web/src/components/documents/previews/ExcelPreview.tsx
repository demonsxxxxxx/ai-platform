/* eslint-disable react-refresh/only-export-components */
import JSZip from "jszip";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { LoadingSpinner } from "../../common/LoadingSpinner";

const excelPreviewStylesPromise =
  typeof document === "undefined"
    ? null
    : import("../../../styles/excel-preview.css");

if (excelPreviewStylesPromise) {
  void excelPreviewStylesPromise;
}

export const EXCEL_PREVIEW_MAX_BYTES = 5 * 1024 * 1024;
export const EXCEL_PREVIEW_TIMEOUT_MS = 1_500;
const EXCEL_PREVIEW_MAX_ENTRY_BYTES = 2 * 1024 * 1024;
const EXCEL_PREVIEW_MAX_TOTAL_UNCOMPRESSED_BYTES = 4 * 1024 * 1024;
const EXCEL_PREVIEW_MAX_SHEETS = 8;
const EXCEL_PREVIEW_MAX_ROWS = 200;
const EXCEL_PREVIEW_MAX_COLS = 50;
const EXCEL_PREVIEW_MAX_CELLS =
  EXCEL_PREVIEW_MAX_ROWS * EXCEL_PREVIEW_MAX_COLS;

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

interface ExcelPreviewProps {
  arrayBuffer: ArrayBuffer;
  fileName: string;
  t: (key: string, options?: Record<string, unknown>) => string;
}

interface SheetData {
  name: string;
  data: string[][];
}

interface WorkbookZipFile {
  async(type: "string"): Promise<string>;
  _data?: {
    compressedSize?: number;
    uncompressedSize?: number;
  };
}

interface WorkbookZipLike {
  file(path: string): WorkbookZipFile | null;
}

interface WorkbookSheetRef {
  name: string;
  relationshipId: string;
}

interface XmlElement {
  name: string;
  localName: string;
  attributes: Map<string, string>;
  children: XmlElement[];
  content: Array<string | XmlElement>;
}

function getFileExtension(value?: string | null): string {
  if (!value) {
    return "";
  }
  const normalized = value.replace(/\\/g, "/");
  const segment = normalized.split("/").pop() ?? "";
  const lastDot = segment.lastIndexOf(".");
  return lastDot <= 0 ? "" : segment.slice(lastDot + 1).toLowerCase();
}

function isSupportedBrowserWorkbook(fileName?: string | null): boolean {
  const extension = getFileExtension(fileName);
  return extension === "xlsx" || extension === "xlsm";
}

function getNow(): number {
  return typeof performance !== "undefined" ? performance.now() : Date.now();
}

function assertPreviewBudget(
  startMs: number,
  timeoutMs: number,
  now: () => number,
): void {
  if (now() - startMs > timeoutMs) {
    throw new Error("excel_preview_timeout");
  }
}

async function loadWorkbookZip(arrayBuffer: ArrayBuffer): Promise<WorkbookZipLike> {
  return JSZip.loadAsync(arrayBuffer);
}

async function readWorkbookText(
  zip: WorkbookZipLike,
  path: string,
  maxEntryBytes = EXCEL_PREVIEW_MAX_ENTRY_BYTES,
): Promise<string | null> {
  const file = zip.file(path);
  if (!file) {
    return null;
  }
  if ((file._data?.uncompressedSize ?? 0) > maxEntryBytes) {
    throw new Error("excel_preview_entry_too_large");
  }
  return file.async("string");
}

function decodeXmlEntities(value: string): string {
  return value.replace(
    /&(#x?[0-9a-fA-F]+|amp|apos|gt|lt|quot);/g,
    (entity, token: string) => {
      switch (token) {
        case "amp":
          return "&";
        case "apos":
          return "'";
        case "gt":
          return ">";
        case "lt":
          return "<";
        case "quot":
          return '"';
        default: {
          const isHex = token.startsWith("#x");
          const isNumeric = token.startsWith("#");
          if (!isNumeric) {
            return entity;
          }
          const codePoint = Number.parseInt(
            token.slice(isHex ? 2 : 1),
            isHex ? 16 : 10,
          );
          return Number.isFinite(codePoint)
            ? String.fromCodePoint(codePoint)
            : entity;
        }
      }
    },
  );
}

const XML_QNAME = /^[A-Za-z_][A-Za-z0-9_.-]*(?::[A-Za-z_][A-Za-z0-9_.-]*)?$/;

function invalidXml(): never {
  throw new Error("excel_preview_invalid_xml");
}

function localName(value: string): string {
  return value.slice(value.indexOf(":") + 1);
}

function findXmlTerminator(
  xml: string,
  startIndex: number,
  terminator: string,
  assertBudget: () => void,
): number {
  for (let index = startIndex; index <= xml.length - terminator.length; index += 1) {
    if (index % 1_024 === 0) {
      assertBudget();
    }
    if (xml.startsWith(terminator, index)) {
      return index;
    }
  }
  return -1;
}

function findXmlTagEnd(
  xml: string,
  startIndex: number,
  assertBudget: () => void,
): number {
  let quote: string | null = null;
  for (let index = startIndex; index < xml.length; index += 1) {
    if (index % 1_024 === 0) {
      assertBudget();
    }
    const char = xml[index];
    if (quote) {
      if (char === quote) {
        quote = null;
      }
    } else if (char === '"' || char === "'") {
      quote = char;
    } else if (char === ">") {
      return index;
    }
  }
  return -1;
}

function parseXmlStartTag(tag: string): {
  element: XmlElement;
  selfClosing: boolean;
} {
  const trimmedTag = tag.trim();
  const selfClosing = trimmedTag.endsWith("/");
  const source = selfClosing
    ? trimmedTag.slice(0, -1).trimEnd()
    : trimmedTag;
  let index = 0;

  const skipWhitespace = () => {
    while (index < source.length && /\s/.test(source[index])) {
      index += 1;
    }
  };
  const readName = () => {
    const start = index;
    while (index < source.length && !/[\s=]/.test(source[index])) {
      index += 1;
    }
    const name = source.slice(start, index);
    if (!XML_QNAME.test(name)) {
      invalidXml();
    }
    return name;
  };

  skipWhitespace();
  const name = readName();
  const attributes = new Map<string, string>();

  while (index < source.length) {
    skipWhitespace();
    if (index >= source.length) {
      break;
    }
    const attributeName = readName();
    skipWhitespace();
    if (source[index] !== "=") {
      invalidXml();
    }
    index += 1;
    skipWhitespace();
    const quote = source[index];
    if (quote !== '"' && quote !== "'") {
      invalidXml();
    }
    index += 1;
    const valueStart = index;
    while (index < source.length && source[index] !== quote) {
      index += 1;
    }
    if (index >= source.length || attributes.has(attributeName)) {
      invalidXml();
    }
    attributes.set(
      attributeName,
      decodeXmlEntities(source.slice(valueStart, index)),
    );
    index += 1;
  }

  return {
    element: {
      name,
      localName: localName(name),
      attributes,
      children: [],
      content: [],
    },
    selfClosing,
  };
}

function parseOoxmlDocument(
  xml: string,
  assertBudget: () => void,
): XmlElement {
  let root: XmlElement | null = null;
  const stack: XmlElement[] = [];
  let index = 0;

  const appendText = (text: string) => {
    if (text.length === 0) {
      return;
    }
    const current = stack.at(-1);
    if (!current) {
      if (text.trim().length > 0) {
        invalidXml();
      }
      return;
    }
    current.content.push(decodeXmlEntities(text));
  };

  while (index < xml.length) {
    assertBudget();
    const tagStart = xml.indexOf("<", index);
    if (tagStart < 0) {
      appendText(xml.slice(index));
      break;
    }
    appendText(xml.slice(index, tagStart));

    if (xml.startsWith("<?", tagStart)) {
      const end = findXmlTerminator(xml, tagStart + 2, "?>", assertBudget);
      if (end < 0) {
        invalidXml();
      }
      index = end + 2;
      continue;
    }
    if (xml.startsWith("<!--", tagStart)) {
      const end = findXmlTerminator(xml, tagStart + 4, "-->", assertBudget);
      if (end < 0) {
        invalidXml();
      }
      index = end + 3;
      continue;
    }
    if (xml.startsWith("<![CDATA[", tagStart)) {
      const end = findXmlTerminator(xml, tagStart + 9, "]]>", assertBudget);
      if (end < 0 || stack.length === 0) {
        invalidXml();
      }
      stack.at(-1)?.content.push(xml.slice(tagStart + 9, end));
      index = end + 3;
      continue;
    }
    if (xml.startsWith("<!", tagStart)) {
      invalidXml();
    }

    const tagEnd = findXmlTagEnd(xml, tagStart + 1, assertBudget);
    if (tagEnd < 0) {
      invalidXml();
    }
    const tag = xml.slice(tagStart + 1, tagEnd);
    if (tag.startsWith("/")) {
      const name = tag.slice(1).trim();
      const current = stack.at(-1);
      if (!XML_QNAME.test(name) || !current || current.name !== name) {
        invalidXml();
      }
      stack.pop();
    } else {
      const { element, selfClosing } = parseXmlStartTag(tag);
      const parent = stack.at(-1);
      if (parent) {
        parent.children.push(element);
        parent.content.push(element);
      } else if (!root) {
        root = element;
      } else {
        invalidXml();
      }
      if (!selfClosing) {
        stack.push(element);
      }
    }
    index = tagEnd + 1;
  }

  if (!root || stack.length > 0) {
    invalidXml();
  }
  return root;
}

function findXmlElements(element: XmlElement, targetLocalName: string): XmlElement[] {
  const matches: XmlElement[] = [];
  const pending = [element];
  while (pending.length > 0) {
    const current = pending.pop();
    if (!current) {
      continue;
    }
    if (current.localName === targetLocalName) {
      matches.push(current);
    }
    for (let index = current.children.length - 1; index >= 0; index -= 1) {
      pending.push(current.children[index]);
    }
  }
  return matches;
}

function getXmlText(element: XmlElement): string {
  return element.content
    .map((entry) => (typeof entry === "string" ? entry : getXmlText(entry)))
    .join("");
}

function extractTagText(element: XmlElement, tagName: string): string {
  const match = findXmlElements(element, tagName)[0];
  return match ? getXmlText(match) : "";
}

function extractInlineText(element: XmlElement): string {
  return findXmlElements(element, "t").map(getXmlText).join("");
}

function extractAttribute(element: XmlElement, attributeName: string): string | null {
  let value: string | null = null;
  for (const [name, candidate] of element.attributes) {
    if (localName(name) !== attributeName) {
      continue;
    }
    if (value != null) {
      return null;
    }
    value = candidate;
  }
  return value;
}

function parseWorkbookSheetRefs(workbook: XmlElement): WorkbookSheetRef[] {
  return findXmlElements(workbook, "sheet")
    .map((sheet) => {
      const name = extractAttribute(sheet, "name");
      const relationshipId = extractAttribute(sheet, "id");
      return name && relationshipId ? { name, relationshipId } : null;
    })
    .filter((value): value is WorkbookSheetRef => value != null);
}

function parseWorkbookRelationships(relationships: XmlElement | null): Map<string, string> {
  if (!relationships) {
    return new Map();
  }
  return new Map(
    findXmlElements(relationships, "Relationship")
      .map((relationship) => {
        const id = extractAttribute(relationship, "Id");
        const target = extractAttribute(relationship, "Target");
        return id && target ? ([id, target] as const) : null;
      })
      .filter((value): value is readonly [string, string] => value != null),
  );
}

function resolveWorkbookTargetPath(target: string): string {
  const normalized = target.replace(/\\/g, "/").replace(/^\.?\//, "");
  return normalized.startsWith("xl/") ? normalized : `xl/${normalized}`;
}

function decodeColumnReference(columnRef: string): number {
  let value = 0;
  for (const char of columnRef) {
    value = value * 26 + (char.charCodeAt(0) - 64);
  }
  return Math.max(0, value - 1);
}

function parseCellValue(
  cellType: string | null,
  cell: XmlElement,
  sharedStrings: string[],
): string {
  if (cellType === "inlineStr") {
    return extractInlineText(cell);
  }
  if (cellType === "s") {
    const index = Number.parseInt(extractTagText(cell, "v"), 10);
    return Number.isFinite(index) ? (sharedStrings[index] ?? "") : "";
  }
  if (cellType === "b") {
    const raw = extractTagText(cell, "v");
    if (raw === "1") return "TRUE";
    if (raw === "0") return "FALSE";
    return raw;
  }
  return extractTagText(cell, "v") || extractInlineText(cell);
}

function parseSharedStrings(sharedStrings: XmlElement | null): string[] {
  if (!sharedStrings) {
    return [];
  }

  return findXmlElements(sharedStrings, "si").map(extractInlineText);
}

function parseWorksheetRows(
  worksheet: XmlElement,
  sharedStrings: string[],
  options: {
    startMs: number;
    now: () => number;
    timeoutMs: number;
    maxRows: number;
    maxCols: number;
    maxCells: number;
  },
): string[][] {
  const rowMap = new Map<number, string[]>();
  let highestRowIndex = -1;
  let highestColIndex = -1;
  let cellCount = 0;
  let fallbackRowIndex = 0;

  for (const row of findXmlElements(worksheet, "row")) {
    assertPreviewBudget(options.startMs, options.timeoutMs, options.now);
    const explicitRowNumber = Number.parseInt(
      extractAttribute(row, "r") ?? "",
      10,
    );
    const rowIndex = Number.isFinite(explicitRowNumber)
      ? explicitRowNumber - 1
      : fallbackRowIndex;
    if (rowIndex >= options.maxRows) {
      throw new Error("excel_preview_limits_exceeded");
    }

    const values = rowMap.get(rowIndex) ?? [];
    let fallbackColIndex = 0;
    for (const cell of row.children.filter(
      (child) => child.localName === "c",
    )) {
      assertPreviewBudget(options.startMs, options.timeoutMs, options.now);
      const refMatch = (extractAttribute(cell, "r") ?? "").match(
        /^([A-Z]+)(\d+)$/i,
      );
      const colIndex = refMatch
        ? decodeColumnReference(refMatch[1].toUpperCase())
        : fallbackColIndex;
      if (colIndex >= options.maxCols) {
        throw new Error("excel_preview_limits_exceeded");
      }

      const cellType = extractAttribute(cell, "t");
      values[colIndex] = parseCellValue(cellType, cell, sharedStrings);
      fallbackColIndex = colIndex + 1;
      highestColIndex = Math.max(highestColIndex, colIndex);
      cellCount += 1;
      if (cellCount > options.maxCells) {
        throw new Error("excel_preview_limits_exceeded");
      }
    }

    rowMap.set(rowIndex, values);
    highestRowIndex = Math.max(highestRowIndex, rowIndex);
    fallbackRowIndex = rowIndex + 1;
  }

  if (highestRowIndex < 0 || highestColIndex < 0) {
    return [];
  }

  const rows: string[][] = [];
  for (let rowIndex = 0; rowIndex <= highestRowIndex; rowIndex += 1) {
    assertPreviewBudget(options.startMs, options.timeoutMs, options.now);
    const source = rowMap.get(rowIndex) ?? [];
    rows.push(
      Array.from(
        { length: highestColIndex + 1 },
        (_, colIndex) => source[colIndex] ?? "",
      ),
    );
  }
  return rows;
}

function mapExcelPreviewError(
  error: unknown,
  t: (key: string, options?: Record<string, unknown>) => string,
): string {
  if (!(error instanceof Error)) {
    return t("documents.excelParseError");
  }

  switch (error.message) {
    case "excel_preview_file_too_large":
      return t("documents.excelPreviewTooLarge", {
        defaultValue:
          "Workbook preview is unavailable because the file exceeds the browser safety size limit.",
      });
    case "excel_preview_limits_exceeded":
      return t("documents.excelPreviewLimitsExceeded", {
        defaultValue:
          "Workbook preview is unavailable because the sheet exceeds the browser safety limits.",
      });
    case "excel_preview_timeout":
      return t("documents.excelPreviewTimeout", {
        defaultValue:
          "Workbook preview timed out before a safe browser preview could be produced.",
      });
    case "excel_preview_entry_too_large":
      return t("documents.excelPreviewEntryTooLarge", {
        defaultValue:
          "Workbook preview is unavailable because the unpacked sheet data exceeds the browser safety limits.",
      });
    case "excel_preview_missing_workbook_xml":
      return t("documents.excelPreviewInvalidWorkbook", {
        defaultValue:
          "Workbook preview is unavailable because the file structure is invalid.",
      });
    case "excel_preview_no_recognized_sheet":
      return t("documents.excelPreviewNoRecognizedSheet", {
        defaultValue:
          "Workbook preview is unavailable because no recognizable worksheet was found.",
      });
    case "excel_preview_invalid_xml":
      return t("documents.excelPreviewInvalidWorkbook", {
        defaultValue:
          "Workbook preview is unavailable because the XML is malformed.",
      });
    case "excel_preview_unsupported_format":
      return t("documents.excelPreviewUnsupportedFormat", {
        defaultValue:
          "This workbook format is not available for in-browser preview. Download the file to inspect it safely.",
      });
    default:
      return error.message || t("documents.excelParseError");
  }
}

/**
 * Build a bounded browser preview for supported OOXML workbooks.
 */
export async function parseExcelWorkbookPreview(
  arrayBuffer: ArrayBuffer,
  options?: {
    loadZip?: (arrayBuffer: ArrayBuffer) => Promise<WorkbookZipLike>;
    fileName?: string;
    now?: () => number;
    timeoutMs?: number;
    maxBytes?: number;
    maxEntryBytes?: number;
    maxTotalUncompressedBytes?: number;
    maxSheets?: number;
    maxRows?: number;
    maxCols?: number;
    maxCells?: number;
  },
): Promise<SheetData[]> {
  if (options?.fileName && !isSupportedBrowserWorkbook(options.fileName)) {
    throw new Error("excel_preview_unsupported_format");
  }

  const maxBytes = options?.maxBytes ?? EXCEL_PREVIEW_MAX_BYTES;
  if (arrayBuffer.byteLength > maxBytes) {
    throw new Error("excel_preview_file_too_large");
  }

  const maxEntryBytes = options?.maxEntryBytes ?? EXCEL_PREVIEW_MAX_ENTRY_BYTES;
  const maxTotalUncompressedBytes =
    options?.maxTotalUncompressedBytes ??
    EXCEL_PREVIEW_MAX_TOTAL_UNCOMPRESSED_BYTES;
  const now = options?.now ?? getNow;
  const timeoutMs = options?.timeoutMs ?? EXCEL_PREVIEW_TIMEOUT_MS;
  const startMs = now();
  const zip = await (options?.loadZip ?? loadWorkbookZip)(arrayBuffer);
  assertPreviewBudget(startMs, timeoutMs, now);

  let totalUncompressedBytes = 0;
  const readBoundedWorkbookText = async (path: string): Promise<string | null> => {
    const file = zip.file(path);
    const entryBytes = file?._data?.uncompressedSize ?? 0;
    if (entryBytes > maxEntryBytes) {
      throw new Error("excel_preview_entry_too_large");
    }
    totalUncompressedBytes += entryBytes;
    if (totalUncompressedBytes > maxTotalUncompressedBytes) {
      throw new Error("excel_preview_entry_too_large");
    }
    return readWorkbookText(zip, path, maxEntryBytes);
  };

  const workbookXml = await readBoundedWorkbookText("xl/workbook.xml");
  if (!workbookXml) {
    throw new Error("excel_preview_missing_workbook_xml");
  }

  const assertXmlBudget = () =>
    assertPreviewBudget(startMs, timeoutMs, now);
  const workbook = parseOoxmlDocument(workbookXml, assertXmlBudget);
  const relationshipsXml =
    (await readBoundedWorkbookText("xl/_rels/workbook.xml.rels")) ?? "";
  const relationshipMap = parseWorkbookRelationships(
    relationshipsXml
      ? parseOoxmlDocument(relationshipsXml, assertXmlBudget)
      : null,
  );
  const sharedStringsXml = await readBoundedWorkbookText("xl/sharedStrings.xml");
  const sharedStrings = parseSharedStrings(
    sharedStringsXml
      ? parseOoxmlDocument(sharedStringsXml, assertXmlBudget)
      : null,
  );
  const sheetRefs = parseWorkbookSheetRefs(workbook);
  if (sheetRefs.length === 0) {
    throw new Error("excel_preview_no_recognized_sheet");
  }
  const sheets = sheetRefs.slice(
    0,
    options?.maxSheets ?? EXCEL_PREVIEW_MAX_SHEETS,
  );

  const parsedSheets: SheetData[] = [];
  for (const sheet of sheets) {
    assertPreviewBudget(startMs, timeoutMs, now);
    const target = relationshipMap.get(sheet.relationshipId);
    if (!target) {
      throw new Error("excel_preview_missing_workbook_xml");
    }
    const sheetXml = await readBoundedWorkbookText(
      resolveWorkbookTargetPath(target),
    );
    if (!sheetXml) {
      throw new Error("excel_preview_missing_workbook_xml");
    }
    parsedSheets.push({
      name: sheet.name,
      data: parseWorksheetRows(parseOoxmlDocument(sheetXml, assertXmlBudget), sharedStrings, {
        startMs,
        now,
        timeoutMs,
        maxRows: options?.maxRows ?? EXCEL_PREVIEW_MAX_ROWS,
        maxCols: options?.maxCols ?? EXCEL_PREVIEW_MAX_COLS,
        maxCells: options?.maxCells ?? EXCEL_PREVIEW_MAX_CELLS,
      }),
    });
  }

  return parsedSheets;
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
  return !isNaN(Number(v));
}

const ExcelPreview = memo(function ExcelPreview({
  arrayBuffer,
  fileName: _fileName,
  t,
}: ExcelPreviewProps) {
  const [sheets, setSheets] = useState<SheetData[]>([]);
  const [activeSheet, setActiveSheet] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [hoveredCell, setHoveredCell] = useState<{
    row: number;
    col: number;
  } | null>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const { progress, hasOverflow } = useScrollIndicator(scrollContainerRef);

  useEffect(() => {
    let cancelled = false;

    const parseExcel = async () => {
      try {
        const sheetData = await parseExcelWorkbookPreview(arrayBuffer, {
          fileName: _fileName,
        });
        if (cancelled) {
          return;
        }
        setSheets(sheetData);
        setActiveSheet(0);
        setError(null);
      } catch (err) {
        console.error("Excel parse error:", err);
        if (!cancelled) {
          setError(mapExcelPreviewError(err, t));
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    void parseExcel();
    return () => {
      cancelled = true;
    };
  }, [arrayBuffer, _fileName, t]);

  const currentSheet = sheets[activeSheet];

  const totalRows = useMemo(() => {
    if (!currentSheet) return 0;
    return currentSheet.data.length;
  }, [currentSheet]);

  const totalCols = useMemo(() => {
    if (!currentSheet || currentSheet.data.length === 0) return 0;
    return currentSheet.data[0].length;
  }, [currentSheet]);

  const headerRow = useMemo(() => {
    if (!currentSheet || currentSheet.data.length === 0) return [];
    return currentSheet.data[0];
  }, [currentSheet]);

  const dataRows = useMemo(() => {
    if (!currentSheet || currentSheet.data.length <= 1) return [];
    return currentSheet.data.slice(1);
  }, [currentSheet]);

  const handleCellHover = useCallback((rowIndex: number, colIndex: number) => {
    setHoveredCell({ row: rowIndex, col: colIndex });
  }, []);

  const handleCellLeave = useCallback(() => {
    setHoveredCell(null);
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <LoadingSpinner
          size="lg"
          className="text-stone-400 dark:text-stone-500"
        />
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800">
        <p className="text-sm text-red-600 dark:text-red-400 font-medium">
          {t("documents.excelPreviewError")}: {error}
        </p>
      </div>
    );
  }

  function getCellValue(rowIndex: number, colIndex: number): string {
    if (rowIndex === -1) {
      return headerRow[colIndex] != null ? String(headerRow[colIndex]) : "";
    }
    if (dataRows[rowIndex]) {
      const v = dataRows[rowIndex][colIndex];
      return v != null && v !== "" ? String(v) : "";
    }
    return "";
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
            ? `${colLabel(hoveredCell.col)}${hoveredCell.row + 1}`
            : "A1"}
        </span>
        <span className="h-4 w-px bg-stone-300 dark:bg-stone-600 shrink-0" />
        <span className="text-[12px] text-stone-700 dark:text-stone-300 truncate">
          {hoveredCell
            ? getCellValue(hoveredCell.row, hoveredCell.col)
            : headerRow.length > 0
              ? String(headerRow[0] ?? "")
              : ""}
        </span>
      </div>

      <div className="flex items-center gap-0.5 px-1 py-0 bg-stone-100 dark:bg-stone-900 border-b border-stone-300 dark:border-stone-700 shrink-0">
        <button
          type="button"
          onClick={() => setActiveSheet((p) => Math.max(0, p - 1))}
          disabled={activeSheet === 0}
          className="p-1 text-stone-400 hover:text-stone-600 dark:hover:text-stone-300 disabled:opacity-30 disabled:cursor-default transition-colors"
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <polyline points="15 18 9 12 15 6" />
          </svg>
        </button>
        <div className="flex-1 flex items-center gap-0.5 overflow-x-auto px-1 py-1">
          {sheets.map((sheet: SheetData, index) => (
            <button
              key={sheet.name}
              onClick={() => setActiveSheet(index)}
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
            setActiveSheet((p) => Math.min(sheets.length - 1, p + 1))
          }
          disabled={activeSheet === sheets.length - 1}
          className="p-1 text-stone-400 hover:text-stone-600 dark:hover:text-stone-300 disabled:opacity-30 disabled:cursor-default transition-colors"
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <polyline points="9 18 15 12 9 6" />
          </svg>
        </button>
      </div>

      <div
        ref={scrollContainerRef}
        className="flex-1 overflow-auto relative overscroll-x-contain [-webkit-overflow-scrolling:touch] excel-preview-scroll border-x border-stone-300 dark:border-stone-600"
      >
        <table className="border-collapse w-max min-w-full text-[13px]">
          <thead>
            <tr className="sticky top-0 z-10">
              <th className="sticky left-0 z-20 w-8 sm:w-10 min-w-[2rem] sm:min-w-[2.5rem] max-w-[2rem] sm:max-w-[2.5rem] px-0 py-0 text-center text-[11px] text-stone-500 dark:text-stone-400 bg-stone-100 dark:bg-stone-800 border-r border-b border-stone-300 dark:border-stone-600 select-none" />
              {Array.from({ length: totalCols }, (_, i) => (
                <th
                  key={i}
                  className={`min-w-[60px] sm:min-w-[80px] h-6 px-0 py-0 text-center text-[11px] font-normal text-stone-500 dark:text-stone-400 bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-600 select-none leading-6 ${
                    hoveredCell?.col === i
                      ? "bg-stone-100 dark:bg-stone-800 text-stone-700 dark:text-stone-300"
                      : ""
                  }`}
                >
                  {colLabel(i)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: displayRows }, (_, rawRowIndex) => {
              const isHeader = rawRowIndex === 0;
              const rowIndex = isHeader ? -1 : rawRowIndex - 1;
              const isRowHovered =
                hoveredCell && !isHeader && hoveredCell.row === rawRowIndex - 1;

              return (
                <tr key={rawRowIndex}>
                  <td
                    className={`sticky left-0 z-10 w-8 sm:w-10 min-w-[2rem] sm:min-w-[2.5rem] max-w-[2rem] sm:max-w-[2.5rem] px-0 py-0 text-center text-[11px] bg-stone-100 dark:bg-stone-800 border-r border-b border-stone-300 dark:border-stone-600 select-none tabular-nums leading-6 touch-none [box-shadow:2px_0_4px_-1px_rgba(0,0,0,0.06)] dark:[box-shadow:2px_0_4px_-1px_rgba(0,0,0,0.3)] ${
                      isHeader
                        ? "text-stone-400 dark:text-stone-500"
                        : isRowHovered
                          ? "text-blue-700 dark:text-blue-300 bg-blue-50 dark:bg-blue-900/40"
                          : "text-stone-500 dark:text-stone-400"
                    }`}
                  >
                    {isHeader ? "" : rawRowIndex}
                  </td>
                  {Array.from({ length: totalCols }, (_, colIndex) => {
                    const value = getCellValue(rowIndex, colIndex);
                    const num = isNumeric(value);
                    const isCellHovered =
                      hoveredCell &&
                      !isHeader &&
                      hoveredCell.row === rawRowIndex - 1 &&
                      hoveredCell.col === colIndex;

                    if (isHeader) {
                      return (
                        <th
                          key={colIndex}
                          onMouseEnter={() => handleCellHover(0, colIndex)}
                          onMouseLeave={handleCellLeave}
                          className={`min-h-[24px] min-w-[60px] sm:min-w-[80px] px-2 py-0 text-[13px] leading-6 border border-stone-300 dark:border-stone-600 whitespace-nowrap text-left font-semibold text-stone-700 dark:text-stone-300 bg-stone-50 dark:bg-stone-800/60 ${
                            isCellHovered
                              ? "!outline outline-2 outline-stone-500 dark:outline-stone-400 outline-offset-[-1px] bg-stone-100/60 dark:bg-stone-800/40 !border-stone-400 dark:!border-stone-500"
                              : ""
                          }`}
                        >
                          {value || " "}
                        </th>
                      );
                    }

                    return (
                      <td
                        key={colIndex}
                        onMouseEnter={() =>
                          handleCellHover(rawRowIndex - 1, colIndex)
                        }
                        onMouseLeave={handleCellLeave}
                        className={`min-h-[24px] min-w-[60px] sm:min-w-[80px] px-2 py-0 text-[13px] leading-6 border border-stone-200 dark:border-stone-700/80 whitespace-nowrap text-stone-800 dark:text-stone-200 ${
                          num
                            ? "text-right tabular-nums font-mono"
                            : "text-left"
                        } ${
                          isCellHovered
                            ? "!outline outline-2 outline-stone-500 dark:outline-stone-400 outline-offset-[-1px] bg-stone-100/60 dark:bg-stone-800/40 !border-stone-400 dark:!border-stone-500"
                            : isRowHovered
                              ? "bg-stone-50/70 dark:bg-stone-800/30"
                              : ""
                        }`}
                      >
                        {value || " "}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>

        {totalRows === 0 && (
          <div className="flex flex-col items-center justify-center py-20 text-stone-400 dark:text-stone-500">
            <p className="text-sm">{t("documents.noData") || "No data"}</p>
          </div>
        )}

        {hasOverflow && (
          <div className="absolute bottom-0 left-0 right-0 h-1 z-30 pointer-events-none">
            <div className="h-full bg-stone-300/40 dark:bg-stone-600/40" />
            <div
              className="absolute top-0 h-full bg-stone-400 dark:bg-stone-500 transition-[left] duration-75"
              style={{
                width: `${Math.max(10, (1 - progress) * 100)}%`,
                left: `${progress * 100}%`,
              }}
            />
          </div>
        )}
      </div>

      <div className="flex items-center justify-between px-3 py-1 text-[11px] text-stone-500 dark:text-stone-400 bg-stone-100 dark:bg-stone-800 border-t border-stone-300 dark:border-stone-600 shrink-0">
        <span className="tabular-nums">
          {sheets.length > 1 && (
            <span className="mr-2 text-stone-400 dark:text-stone-500">
              {currentSheet?.name}
            </span>
          )}
          {t("documents.excelRowsAndCols", {
            rows: dataRows.length,
            cols: totalCols,
          })}
        </span>
        <div className="flex items-center gap-3">
          {hoveredCell && (
            <span className="px-1.5 rounded bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-400 font-mono">
              {colLabel(hoveredCell.col)}
              {hoveredCell.row + 1}
            </span>
          )}
          <span className="text-stone-400 dark:text-stone-500">
            {t("documents.excelReady")}
          </span>
        </div>
      </div>
    </div>
  );
});

export default ExcelPreview;
