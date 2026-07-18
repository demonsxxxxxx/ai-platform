import assert from "node:assert/strict";
import test from "node:test";

import {
  getFileTypeInfo,
  getFileLinkInfo,
  isCadFile,
  isCsvFile,
  isDwgFile,
  isDxfFile,
  isFileLink,
  isPreviewableFile,
  isServerXlsxPreviewFile,
} from "../index";

Object.defineProperty(globalThis, "window", {
  value: { location: { origin: "https://example.test" } },
  configurable: true,
});

test("recognizes CAD file extensions", () => {
  assert.equal(isCadFile("dxf"), true);
  assert.equal(isCadFile("dwg"), true);
  assert.equal(isDxfFile("dxf"), true);
  assert.equal(isDxfFile("dwg"), false);
  assert.equal(isDwgFile("dwg"), true);
  assert.equal(isDwgFile("dxf"), false);
});

test("treats CAD files as previewable links", () => {
  assert.equal(isPreviewableFile("dxf"), true);
  assert.equal(isPreviewableFile("dwg"), true);

  const dxfLink = isFileLink("/uploads/site-plan.dxf");
  assert.equal(dxfLink.isFile, true);
  assert.equal(dxfLink.fileName, "site-plan.dxf");

  const dwgLink = isFileLink("/uploads/site-plan.dwg");
  assert.equal(dwgLink.isFile, true);
  assert.equal(dwgLink.fileName, "site-plan.dwg");
});

test("does not mark ebook formats as previewable without a renderer", () => {
  assert.equal(isPreviewableFile("epub"), false);
  assert.equal(isPreviewableFile("mobi"), false);
});

test("routes only .xlsx to the server DTO while preserving CSV text preview", () => {
  assert.equal(isServerXlsxPreviewFile("xlsx"), true);
  assert.equal(isServerXlsxPreviewFile("xlsm"), false);
  assert.equal(isCsvFile("csv"), true);
  assert.equal(isPreviewableFile("xlsx"), true);
  assert.equal(isPreviewableFile("csv"), true);
  assert.equal(isPreviewableFile("xls"), false);
  assert.equal(isPreviewableFile("xlsm"), false);
  assert.equal(isPreviewableFile("xlsb"), false);
  assert.equal(isPreviewableFile("ods"), false);
});

test("recognizes CAD links from labels and download query metadata", () => {
  const labelled = getFileLinkInfo(
    "/api/upload/file/opaque-key",
    "site-plan.dwg",
  );
  assert.equal(labelled.isFile, true);
  assert.equal(labelled.fileName, "site-plan.dwg");

  const queryNamed = getFileLinkInfo(
    "/api/upload/file/opaque-key?filename=site-plan.dxf",
  );
  assert.equal(queryNamed.isFile, true);
  assert.equal(queryNamed.fileName, "site-plan.dxf");
});

test("maps CAD files to a document-style file type", () => {
  const dxfInfo = getFileTypeInfo("site-plan.dxf");
  assert.equal(dxfInfo.label, "DXF");
  assert.equal(dxfInfo.category, "document");

  const dwgInfo = getFileTypeInfo("site-plan.dwg");
  assert.equal(dwgInfo.label, "DWG");
  assert.equal(dwgInfo.category, "document");
});
