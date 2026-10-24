import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function readSource(relativePath: string): string {
  return readFileSync(new URL(relativePath, import.meta.url), "utf8");
}

test("chat markdown rendering does not statically import CodeMirrorViewer", () => {
  const source = readSource("../ChatMessage/MarkdownRenderer.tsx");

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
  assert.doesNotMatch(revealPreviewHost, /LazyProjectPreview/);
});

test("Markdown keeps the shared KaTeX and deferred CodeMirror owners without rehype-highlight", () => {
  const packageJson = JSON.parse(readSource("../../../../package.json"));
  const vite = readSource("../../../../vite.config.ts");
  const markdown = readSource("../ChatMessage/MarkdownRenderer.tsx");
  const assistantMarkdown = readSource("../assistant-ui/elements/markdown-text.tsx");
  const documentMarkdown = readSource("../../documents/previews/MarkdownRenderer.tsx");
  assert.equal(packageJson.dependencies["rehype-highlight"], undefined);
  assert.doesNotMatch(vite, /["']rehype-highlight["']/);
  assert.match(markdown, /MARKDOWN_REHYPE_PLUGINS = \[rehypeKatex\]/);
  assert.match(markdown, /DeferredCodeMirrorViewer/);
  assert.match(markdown, /MermaidDiagram/);
  assert.match(assistantMarkdown, /MARKDOWN_REHYPE_PLUGINS/);
  assert.match(assistantMarkdown, /CodeSyntaxHighlighter/);
  assert.match(documentMarkdown, /ChatMarkdownRenderer/);
});
