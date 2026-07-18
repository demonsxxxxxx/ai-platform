import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  buildFileCardPreview,
  getImagePreviewNavigation,
  getPreviewableImageFiles,
  getSessionNavigationTarget,
  resolveSafeRevealedFilePreviewUrl,
} from "../utils.ts";
import type { RevealedFileItem } from "../../../services/api";

function readSource(relativePath: string): string {
  return readFileSync(new URL(relativePath, import.meta.url), "utf8");
}

function createFile(
  overrides: Partial<RevealedFileItem> = {},
): RevealedFileItem {
  return {
    id: overrides.id ?? "file-1",
    file_key: overrides.file_key ?? "revealed/file-1",
    file_name: overrides.file_name ?? "demo.txt",
    file_type: overrides.file_type ?? "document",
    mime_type: overrides.mime_type ?? "text/plain",
    file_size: overrides.file_size ?? 12,
    preview_url: overrides.preview_url ?? null,
    download_url: overrides.download_url ?? null,
    url: overrides.url ?? null,
    session_id: overrides.session_id ?? "session-1",
    session_name: overrides.session_name ?? "Session 1",
    trace_id: overrides.trace_id ?? "trace-1",
    project_id: overrides.project_id ?? null,
    user_id: overrides.user_id ?? "user-1",
    source: overrides.source ?? "reveal_file",
    description: overrides.description ?? null,
    original_path: overrides.original_path ?? "/tmp/demo.txt",
    created_at: overrides.created_at ?? "2026-04-25T00:00:00.000Z",
    is_favorite: overrides.is_favorite ?? false,
    card_preview: overrides.card_preview,
    project_meta: overrides.project_meta,
  };
}

test("uses the first file in the session group as the navigation target", () => {
  const files = [
    createFile({ id: "latest", file_name: "latest.txt" }),
    createFile({ id: "older", file_name: "older.txt" }),
  ];

  assert.equal(getSessionNavigationTarget(files)?.id, "latest");
});

test("returns null when a session group has no files", () => {
  assert.equal(getSessionNavigationTarget([]), null);
});

test("collects previewable images from visible session groups in render order", () => {
  const first = createFile({
    id: "first",
    file_name: "first.png",
    file_type: "image",
    preview_url: "/api/ai/artifacts/first/preview",
  });
  const second = createFile({
    id: "second",
    file_name: "second.webp",
    file_type: "document",
    preview_url: "/api/upload/file/second.webp",
  });
  const missingUrl = createFile({
    id: "missing-url",
    file_name: "missing.png",
    file_type: "image",
    preview_url: null,
  });
  const document = createFile({
    id: "document",
    file_name: "notes.pdf",
    file_type: "document",
    preview_url: "/files/notes.pdf",
  });

  const files = getPreviewableImageFiles([
    { files: [first, missingUrl] },
    { files: [document, second] },
  ]);

  assert.deepEqual(
    files.map((file) => file.id),
    ["first", "second"],
  );
});

test("filters unsafe image preview urls from visible session groups", () => {
  const encodedSettingsUrl = encodeURIComponent(
    encodeURIComponent(".claude/settings.png"),
  );
  const safeApi = createFile({
    id: "safe-api",
    file_name: "safe.png",
    file_type: "image",
    preview_url: "/api/ai/artifacts/safe/preview",
  });
  const safeHttps = createFile({
    id: "safe-https",
    file_name: "safe.webp",
    file_type: "document",
    preview_url: "https://cdn.example.com/safe.webp",
  });
  const unsafeHttp = createFile({
    id: "unsafe-http",
    file_name: "http.png",
    file_type: "image",
    preview_url: "http://cdn.example.com/http.png",
  });
  const unsafeInternal = createFile({
    id: "unsafe-internal",
    file_name: "settings.png",
    file_type: "image",
    preview_url: `/api/ai/artifacts/${encodedSettingsUrl}/preview`,
  });

  const files = getPreviewableImageFiles([
    { files: [safeApi, unsafeHttp, unsafeInternal, safeHttps] },
  ]);

  assert.deepEqual(
    files.map((file) => file.id),
    ["safe-api"],
  );
});

test("revealed file preview urls fail closed to the artifact/file allowlist", () => {
  assert.equal(
    resolveSafeRevealedFilePreviewUrl("/api/ai/artifacts/report/download"),
    "/api/ai/artifacts/report/download",
  );
  assert.equal(
    resolveSafeRevealedFilePreviewUrl("/api/ai/artifacts/report/preview"),
    "/api/ai/artifacts/report/preview",
  );
  assert.equal(
    resolveSafeRevealedFilePreviewUrl("/api/upload/file/report.png"),
    "/api/upload/file/report.png",
  );

  assert.equal(resolveSafeRevealedFilePreviewUrl("/api/users"), null);
  assert.equal(resolveSafeRevealedFilePreviewUrl("/api/chat/stream"), null);
  assert.equal(
    resolveSafeRevealedFilePreviewUrl("https://cdn.example.com/report.png"),
    null,
  );
  assert.equal(
    resolveSafeRevealedFilePreviewUrl(
      "http://127.0.0.1:8020/api/upload/file/report.png",
    ),
    null,
  );
  assert.equal(resolveSafeRevealedFilePreviewUrl("runtime.path"), null);
});

test("revealed image selection ignores legacy and download URLs without preview_url", () => {
  const legacyOnly = createFile({
    id: "legacy-only",
    file_name: "legacy.png",
    file_type: "image",
    url: "/api/ai/artifacts/legacy-only/preview",
    download_url: "/api/ai/artifacts/legacy-only/download",
  });

  assert.deepEqual(getPreviewableImageFiles([{ files: [legacyOnly] }]), []);
  assert.notEqual(buildFileCardPreview(legacyOnly).kind, "image");
});

test("buildFileCardPreview strips unsafe image card URLs before thumbnail render", () => {
  const encodedRunsUrl = encodeURIComponent(
    encodeURIComponent(".claude/runs/run-1/private.png"),
  );

  const unsafeImageFilePreview = buildFileCardPreview(
    createFile({
      file_name: "private.png",
      file_type: "image",
      mime_type: "image/png",
      preview_url: `/api/ai/artifacts/${encodedRunsUrl}/preview`,
    }),
  );

  assert.equal(unsafeImageFilePreview.kind, "image");
  assert.equal(unsafeImageFilePreview.imageUrl, undefined);

  const unsafeStoredPreview = buildFileCardPreview(
    createFile({
      file_name: "stored.png",
      file_type: "image",
      mime_type: "image/png",
      preview_url: "/api/ai/artifacts/safe/preview",
      card_preview: {
        kind: "image",
        title: "Stored preview",
        subtitle: "Generated image",
        badge: "PNG",
        image_url: "http://cdn.example.com/stored.png",
      },
    }),
  );

  assert.equal(unsafeStoredPreview.kind, "image");
  assert.equal(unsafeStoredPreview.imageUrl, undefined);

  const externalHttpsImageFilePreview = buildFileCardPreview(
    createFile({
      file_name: "remote.png",
      file_type: "image",
      mime_type: "image/png",
      preview_url: "https://cdn.example.com/remote.png",
    }),
  );

  assert.equal(externalHttpsImageFilePreview.kind, "image");
  assert.equal(externalHttpsImageFilePreview.imageUrl, undefined);

  const externalHttpsStoredPreview = buildFileCardPreview(
    createFile({
      file_name: "stored-https.png",
      file_type: "image",
      mime_type: "image/png",
      preview_url: "/api/ai/artifacts/safe/preview",
      card_preview: {
        kind: "image",
        title: "Stored https preview",
        subtitle: "Generated image",
        badge: "PNG",
        image_url: "https://cdn.example.com/stored.png",
      },
    }),
  );

  assert.equal(externalHttpsStoredPreview.kind, "image");
  assert.equal(externalHttpsStoredPreview.imageUrl, undefined);
});

test("file library panel guards image and video viewer urls", () => {
  const source = readSource("../RevealedFilesPanel.tsx");

  assert.match(source, /resolveSafeRevealedFilePreviewUrl/);
  assert.match(source, /useSafeAttachmentImageSrc/);
  assert.match(source, /useSafeAttachmentObjectUrl/);
  assert.doesNotMatch(source, /getFullUrl\(safeActiveImageUrl\)/);
  assert.doesNotMatch(source, /setVideoViewerSrc\(getFullUrl\(safeVideoSrc\)/);
  assert.doesNotMatch(source, /setVideoViewerSrc\(getFullUrl\(file\.url\)/);
});

test("resolves previous and next image preview files with boundary states", () => {
  const files = [
    createFile({ id: "first", file_name: "first.png" }),
    createFile({ id: "second", file_name: "second.png" }),
    createFile({ id: "third", file_name: "third.png" }),
  ];

  assert.deepEqual(getImagePreviewNavigation(files, "first"), {
    current: files[0],
    previous: null,
    next: files[1],
    index: 0,
    total: 3,
  });
  assert.deepEqual(getImagePreviewNavigation(files, "second"), {
    current: files[1],
    previous: files[0],
    next: files[2],
    index: 1,
    total: 3,
  });
  assert.deepEqual(getImagePreviewNavigation(files, "third"), {
    current: files[2],
    previous: files[1],
    next: null,
    index: 2,
    total: 3,
  });
});

test("builds a markdown card preview from existing revealed file metadata", () => {
  const preview = buildFileCardPreview(
    createFile({
      file_name: "mermaid-sdlc.md",
      mime_type: "text/markdown",
      description: "生成一个好看的mermaid",
    }),
  );

  assert.equal(preview.kind, "markdown");
  assert.equal(preview.badge, "Markdown");
  assert.equal(preview.title, "mermaid-sdlc");
  assert.equal(preview.subtitle, "生成一个好看的mermaid");
  assert.deepEqual(preview.lines.slice(0, 2), [
    "mermaid-sdlc",
    "生成一个好看的mermaid",
  ]);
});

test("builds a project card preview without fetching project files", () => {
  const preview = buildFileCardPreview(
    createFile({
      file_name: "demo-app",
      file_type: "project",
      source: "reveal_project",
      project_meta: {
        template: "react",
        entry: "/src/main.tsx",
        file_count: 12,
        files: {
          "/src/main.tsx": { url: "/file/main", size: 10 },
        },
      },
    }),
  );

  assert.equal(preview.kind, "project");
  assert.equal(preview.badge, "REACT");
  assert.equal(preview.subtitle, "12 files");
  assert.deepEqual(preview.lines, [
    "▸ Entry /src/main.tsx",
    "· 12 files indexed",
  ]);
});

test("file library document previews fill the mobile viewport like chat previews", () => {
  const source = readSource("../RevealedFilesPanel.tsx");

  assert.match(
    source,
    /<DocumentPreview[\s\S]*?\bmobileFillViewport\b[\s\S]*?\/>/,
  );
});

test("file library document previews do not pass raw revealed file urls to DocumentPreview", () => {
  const source = readSource("../RevealedFilesPanel.tsx");

  assert.match(source, /safePreviewFileUrl/);
  assert.match(source, /previewUrl=\{/);
  assert.match(source, /downloadUrl=\{/);
  assert.doesNotMatch(
    source,
    /signedUrl=\{\s*previewFile\.url\s*\?\s*getFullUrl\(previewFile\.url\)/,
  );
  assert.doesNotMatch(source, /previewFile\?\.url/);
});

test("file context menu keeps explicit revealed preview and download actions separate", () => {
  const source = readSource("../components/FileContextMenu.tsx");

  assert.match(source, /resolveSafeRevealedFilePreviewUrl/);
  assert.match(source, /safePreviewUrl/);
  assert.match(source, /safeDownloadUrl/);
  assert.match(source, /downloadPreviewUrl/);
  assert.match(source, /openPreviewUrl/);
  assert.match(source, /fileName:\s*file\.file_name/);
  assert.match(source, /file\.preview_url/);
  assert.match(source, /file\.download_url/);
  assert.doesNotMatch(source, /file\.url/);
  assert.doesNotMatch(source, /getFullUrl\(file\.url!\)/);
  assert.doesNotMatch(source, /window\.open\(\s*getFullUrl/);
});

test("file library code preview uses authenticated document fetch", () => {
  const source = readSource("../hooks/useCodePreview.ts");

  assert.match(source, /fetchDocumentText/);
  assert.match(source, /clearCodePreviewCache/);
  assert.doesNotMatch(source, /getFullUrl/);
  assert.doesNotMatch(source, /fetch\(fullUrl\)/);
});

test("file card image thumbnails use authenticated blob URLs", () => {
  const source = readSource("../components/FileCardPreview.tsx");

  assert.match(source, /useSafeAttachmentImageSrc/);
  assert.doesNotMatch(source, /getFullUrl\(safeImageUrl\)/);
});

test("file library image previews wire gallery navigation into ImageViewer", () => {
  const source = readSource("../RevealedFilesPanel.tsx");

  assert.match(source, /getPreviewableImageFiles/);
  assert.match(source, /getImagePreviewNavigation/);
  assert.match(source, /<ImageViewer[\s\S]*?\bonPrevious=/);
  assert.match(source, /<ImageViewer[\s\S]*?\bonNext=/);
  assert.match(source, /<ImageViewer[\s\S]*?\bhasPrevious=/);
  assert.match(source, /<ImageViewer[\s\S]*?\bhasNext=/);
});

test("ImageViewer exposes previous and next controls with keyboard shortcuts", () => {
  const source = readSource("../../common/ImageViewer.tsx");

  assert.match(source, /onPrevious\?:/);
  assert.match(source, /onNext\?:/);
  assert.match(source, /hasPrevious\?:/);
  assert.match(source, /hasNext\?:/);
  assert.match(source, /ArrowLeft/);
  assert.match(source, /ArrowRight/);
  assert.match(source, /ChevronLeft/);
  assert.match(source, /ChevronRight/);
});

test("ImageViewer shows a loading affordance while switched images load", () => {
  const source = readSource("../../common/ImageViewer.tsx");

  assert.match(source, /isImageLoading/);
  assert.match(source, /setIsImageLoading\(true\)/);
  assert.match(source, /onLoad=\{\(\) => setIsImageLoading\(false\)\}/);
  assert.match(source, /onError=\{\(\) => setIsImageLoading\(false\)\}/);
  assert.match(source, /imageViewer\.loading/);
  assert.match(source, /animate-spin/);
});
