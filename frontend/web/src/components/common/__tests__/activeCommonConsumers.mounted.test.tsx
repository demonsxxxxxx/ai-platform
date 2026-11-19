import assert from "node:assert/strict";
import test from "node:test";
import type { ComponentProps } from "react";
import { installSelectTestDom } from "./selectTestDom";
import { createLobeTestRoot } from "./lobeUiTestRoot";

const dom = installSelectTestDom();
test.after(() => dom.close());
const { act, createElement } = await import("react");
await import("../../../i18n");
const { SkillsList } = await import("../../panels/SkillsPanel/SkillsList");
const { BinaryFilePreview } = await import("../../skill/BinaryFilePreview");

test("active SkillsList replaces retained card skeletons with the governed table", async () => {
  const container = document.createElement("div");
  document.body.append(container);
  const root = await createLobeTestRoot(container);
  const noop = () => {};
  const entry = {
    id: "synthetic-skill", displayName: "Synthetic skill", description: "Fixture",
    runtimeSkill: null, adminSkill: null, actionName: null, version: null,
    fileCount: null, runtimeEnabled: null, catalogStatus: "available" as const,
    tags: [], updatedAt: null, publishedCatalogName: null,
  };
  const props: ComponentProps<typeof SkillsList> = {
    searchQuery: "", setSearchQuery: noop, selectedTags: [], isFilterOpen: false,
    setIsFilterOpen: noop, availableTags: [], catalogEntries: [entry],
    metricsCatalogEntries: [entry], paginatedCatalogEntries: [entry], total: 1,
    page: 1, pageSize: 10, setPage: noop, setPageSize: noop, catalogView: "all",
    setCatalogView: noop, toggleTag: noop, clearFilters: noop, isLoading: true,
    error: null, clearError: noop, canWrite: false, canEdit: false, canExport: false,
    canImport: false, canBatch: false, canDelete: false, adminRelease: false,
    selectedNames: new Set(), selectedDetail: null, selectedSkillId: null,
    onToggle: noop, onEdit: noop, onDelete: noop, onExportZip: noop,
    onSelectSkill: noop, onSelectAll: noop, onSelectDetail: noop, onZipClick: noop,
  };
  try {
    await act(async () => root.render(createElement(SkillsList, props)));
    assert.equal(container.querySelectorAll(".scb").length, 9);
    assert.equal(container.querySelectorAll(".scb__icon-ring").length, 9);
    assert.equal(container.querySelector("[data-skill-management-table]"), null);
    await act(async () => root.render(createElement(SkillsList, { ...props, isLoading: false })));
    assert.equal(container.querySelectorAll(".scb").length, 0);
    assert.ok(container.querySelector("[data-skill-management-table]"));
    assert.match(container.textContent ?? "", /Synthetic skill/);
  } finally {
    await act(async () => root.unmount());
    container.remove();
  }
});

test("active skill binary video retains native controls and loading completion", async () => {
  const container = document.createElement("div");
  document.body.append(container);
  const root = await createLobeTestRoot(container);
  try {
    await act(async () => root.render(createElement(BinaryFilePreview, {
      url: "https://example.invalid/synthetic.mp4", mime_type: "video/mp4",
      size: 32, fileName: "synthetic.mp4",
    })));
    const video = container.querySelector("video");
    assert.ok(video);
    assert.equal(video.getAttribute("src"), "https://example.invalid/synthetic.mp4");
    assert.equal(video.controls, true);
    assert.equal(video.autoplay, false);
    assert.equal(video.preload, "metadata");
    assert.ok(video.classList.contains("opacity-0"));
    await act(async () => video.dispatchEvent(new window.Event("loadeddata", { bubbles: true })));
    assert.ok(video.classList.contains("opacity-100"));
    assert.equal(container.querySelector("img"), null);
  } finally {
    await act(async () => root.unmount());
    container.remove();
  }
});
