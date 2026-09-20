import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function readSource(relativePath: string): string {
  return readFileSync(new URL(relativePath, import.meta.url), "utf8");
}

test("chat markdown rendering does not statically import CodeMirrorViewer", () => {
  const source = readSource("../ChatMessage/MarkdownContent.tsx");

  assert.doesNotMatch(
    source,
    /import\s+\{?\s*CodeMirrorViewer\s*\}?\s+from\s+"..\/..\/common\/CodeMirrorViewer";/,
  );
  assert.match(source, /DeferredCodeMirrorViewer/);
});

test("chat preview hosts do not statically import heavy preview panels", () => {
  const attachmentPreviewHost = readSource("../AttachmentPreviewHost.tsx");
  assert.doesNotMatch(
    attachmentPreviewHost,
    /import\s+DocumentPreview\s+from\s+"..\/documents\/DocumentPreview";/,
  );
  assert.match(attachmentPreviewHost, /LazyDocumentPreview/);

  const revealPreviewHost = readSource(
    "../ChatMessage/items/RevealPreviewHost.tsx",
  );
  assert.doesNotMatch(
    revealPreviewHost,
    /import\s+DocumentPreview\s+from\s+"..\/..\/..\/documents\/DocumentPreview";/,
  );
  assert.doesNotMatch(
    revealPreviewHost,
    /import\s+ProjectPreview\s+from\s+"..\/..\/..\/documents\/previews\/ProjectPreview";/,
  );
  assert.match(revealPreviewHost, /LazyDocumentPreview/);
  assert.match(revealPreviewHost, /LazyProjectPreview/);
});
