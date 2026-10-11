import { memo, useEffect, useMemo, useState } from "react";
import { FileText, AlertCircle } from "lucide-react";
import { prepareWordPreviewDocument } from "./wordHtmlPreview";
import {
  docxTextToHtml,
  extractDocxTextFallback,
  isDocxSafeForMammoth,
} from "./wordPreviewUtils";
import {
  renderDocxPreviewHtml,
} from "./wordPreviewRenderer";
import {
  extractLegacyDocText,
  isLegacyDocArrayBuffer,
} from "./legacyDocPreviewUtils";
import {
  DocumentViewerFrame,
  ScaledDocumentContent,
} from "./DocumentViewerFrame";

interface WordPreviewProps {
  arrayBuffer: ArrayBuffer;
  t: (key: string, options?: Record<string, unknown>) => string;
}

// Custom styles for Word document content
const wordContentStyles = `
  .docx-preview-content .docx-wrapper {
    width: max-content;
    background: transparent;
    padding: 0;
    gap: 20px;
    align-items: flex-start;
  }
  .docx-preview-content .docx-wrapper > section.docx {
    margin: 0;
    background: white;
    box-shadow: 0 10px 28px rgba(28, 25, 23, 0.16), 0 0 0 1px rgba(28, 25, 23, 0.08);
  }
  .docx-preview-content section.docx img {
    max-width: none;
  }
  .word-preview-content {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
    line-height: 1.7;
    color: #1f2937;
  }
  .word-preview-content.dark {
    color: #e5e7eb;
  }
  .word-preview-content h1 {
    font-size: 2rem;
    font-weight: 700;
    margin: 1.5rem 0 1rem;
    padding-bottom: 0.5rem;
    border-bottom: 2px solid #e5e7eb;
  }
  .word-preview-content.dark h1 {
    border-bottom-color: #374151;
  }
  .word-preview-content h2 {
    font-size: 1.5rem;
    font-weight: 600;
    margin: 1.25rem 0 0.75rem;
    padding-bottom: 0.25rem;
    border-bottom: 1px solid #e5e7eb;
  }
  .word-preview-content.dark h2 {
    border-bottom-color: #374151;
  }
  .word-preview-content h3 {
    font-size: 1.25rem;
    font-weight: 600;
    margin: 1rem 0 0.5rem;
  }
  .word-preview-content h4, .word-preview-content h5, .word-preview-content h6 {
    font-size: 1.125rem;
    font-weight: 600;
    margin: 0.75rem 0 0.5rem;
  }
  .word-preview-content p {
    margin: 0.75rem 0;
  }
  .word-preview-content ul, .word-preview-content ol {
    margin: 0.75rem 0;
    padding-left: 1.5rem;
  }
  .word-preview-content li {
    margin: 0.25rem 0;
  }
  .word-preview-content ul {
    list-style-type: disc;
  }
  .word-preview-content ol {
    list-style-type: decimal;
  }
  .word-preview-content blockquote {
    margin: 1rem 0;
    padding: 0.75rem 1rem;
    border-left: 4px solid #f59e0b;
    background: #fffbeb;
    border-radius: 0 0.5rem 0.5rem 0;
  }
  .word-preview-content.dark blockquote {
    background: rgba(245, 158, 11, 0.1);
    border-left-color: #fbbf24;
  }
  .word-preview-content table {
    width: 100%;
    margin: 1rem 0;
    border-collapse: collapse;
    font-size: 0.8125rem;
  }
  .word-preview-content th {
    background: #f3f4f6;
    padding: 0.5rem 0.75rem;
    text-align: left;
    font-weight: 600;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.025em;
    border: 1px solid #d1d5db;
    color: #374151;
  }
  .word-preview-content.dark th {
    background: #1f2937;
    border-color: #374151;
    color: #9ca3af;
  }
  .word-preview-content td {
    padding: 0.4rem 0.75rem;
    border: 1px solid #e5e7eb;
    color: #1f2937;
  }
  .word-preview-content.dark td {
    border-color: #374151;
    color: #d1d5db;
  }
  .word-preview-content tr:nth-child(even) td {
    background: #f9fafb;
  }
  .word-preview-content.dark tr:nth-child(even) td {
    background: rgba(31, 41, 55, 0.5);
  }
  .word-preview-content tr:hover td {
    background: #f5f5f4;
  }
  .word-preview-content.dark tr:hover td {
    background: rgba(120, 113, 108, 0.08);
  }
  .word-preview-content img {
    max-width: 100%;
    height: auto;
    margin: 1rem 0;
    border-radius: 0.5rem;
    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
  }
  .word-preview-content a {
    color: #2563eb;
    text-decoration: underline;
  }
  .word-preview-content.dark a {
    color: #60a5fa;
  }
  .word-preview-content a:hover {
    color: #1d4ed8;
  }
  .word-preview-content.dark a:hover {
    color: #93c5fd;
  }
  .word-preview-content code {
    font-family: 'SF Mono', Monaco, 'Cascadia Code', monospace;
    font-size: 0.875em;
    background: #f3f4f6;
    padding: 0.125rem 0.375rem;
    border-radius: 0.25rem;
    color: #be185d;
  }
  .word-preview-content.dark code {
    background: #374151;
    color: #f472b6;
  }
  .word-preview-content pre {
    background: #1f2937;
    color: #e5e7eb;
    padding: 1rem;
    border-radius: 0.5rem;
    overflow-x: auto;
    margin: 1rem 0;
  }
  .word-preview-content pre code {
    background: transparent;
    padding: 0;
    color: inherit;
  }
  .word-preview-content hr {
    border: none;
    height: 1px;
    background: linear-gradient(to right, transparent, #e5e7eb, transparent);
    margin: 1.5rem 0;
  }
  .word-preview-content.dark hr {
    background: linear-gradient(to right, transparent, #374151, transparent);
  }
`;

const DEFAULT_DOCX_SIZE = {
  width: 816,
  height: 1056,
};

const WordPreview = memo(function WordPreview({
  arrayBuffer,
  t,
}: WordPreviewProps) {
  const [html, setHtml] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [renderedWithDocxPreview, setRenderedWithDocxPreview] = useState(false);

  // Detect dark mode
  const [isDark, setIsDark] = useState(() =>
    typeof window !== "undefined"
      ? document.documentElement.classList.contains("dark")
      : false,
  );

  useEffect(() => {
    const observer = new MutationObserver(() => {
      setIsDark(document.documentElement.classList.contains("dark"));
    });
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let cancelled = false;

    const convertWord = async () => {
      const renderText = (text: string) => {
        if (cancelled) return false;
        if (text.trim()) {
          setHtml(docxTextToHtml(text));
          setError(null);
          return true;
        }
        return false;
      };

      const renderDocxTextFallback = async () => {
        const fallbackText = await extractDocxTextFallback(arrayBuffer);
        return renderText(fallbackText);
      };

      try {
        setLoading(true);
        setError(null);
        setHtml("");
        setRenderedWithDocxPreview(false);

        if (isLegacyDocArrayBuffer(arrayBuffer)) {
          const legacyText = await extractLegacyDocText(arrayBuffer);
          if (!renderText(legacyText)) {
            if (!cancelled) setError(t("documents.wordConversionError"));
          }
          return;
        }

        const [{ renderAsync }, mammoth] = await Promise.all([
          import("docx-preview"),
          import("mammoth"),
        ]);
        const renderResult = await renderDocxPreviewHtml({
          arrayBuffer,
          renderAsync,
          convertToHtml: async (input, options) => {
            if (await isDocxSafeForMammoth(input.arrayBuffer)) {
              return mammoth.default.convertToHtml(input, options);
            }

            const fallbackText = await extractDocxTextFallback(
              input.arrayBuffer,
            );
            if (!fallbackText.trim()) {
              throw new Error("DOCX fallback did not contain readable text");
            }
            return { value: docxTextToHtml(fallbackText) };
          },
        });
        if (cancelled) return;

        setHtml(renderResult.html);
        setRenderedWithDocxPreview(renderResult.kind === "docx-preview");
        setError(null);
      } catch (err) {
        try {
          if (
            !isLegacyDocArrayBuffer(arrayBuffer) &&
            (await renderDocxTextFallback())
          ) {
            return;
          }
        } catch (fallbackErr) {
          console.error("Failed to extract DOCX text fallback:", fallbackErr);
        }
        console.error("Failed to convert Word document:", err);
        if (!cancelled) setError(t("documents.wordConversionError"));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    convertWord();
    return () => {
      cancelled = true;
    };
  }, [arrayBuffer, t]);

  const previewDocument = useMemo(() => prepareWordPreviewDocument({
    html,
    styles: wordContentStyles,
    dark: isDark && !renderedWithDocxPreview,
    className: renderedWithDocxPreview
      ? "docx-preview-content"
      : `word-preview-content${isDark ? " dark" : ""}`,
  }), [html, isDark, renderedWithDocxPreview]);

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-full min-h-[300px] p-4 sm:p-6">
        <div className="max-w-sm sm:max-w-md w-full">
          <div className="flex items-start gap-3 p-3 sm:p-4 mb-4 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800">
            <AlertCircle
              size={24}
              className="text-red-500 flex-shrink-0 mt-0.5"
            />
            <div className="min-w-0">
              <p className="text-sm font-medium text-red-600 dark:text-red-400">
                {t("documents.wordPreviewError")}
              </p>
              <p className="text-xs text-red-500 dark:text-red-400/80 mt-1 break-words">
                {error}
              </p>
            </div>
          </div>
          <div className="flex items-center justify-center gap-2 text-stone-400 dark:text-stone-500">
            <FileText size={16} />
            <span className="text-xs">
              {t("documents.supportedFormats") || "Word documents (.docx)"}
            </span>
          </div>
        </div>
      </div>
    );
  }

  return (
    <DocumentViewerFrame
      key={loading || renderedWithDocxPreview ? "docx" : "fallback"}
      naturalWidth={DEFAULT_DOCX_SIZE.width}
      loading={loading}
      ariaLabel={t("documents.wordPreviewTitle") || "Word preview"}
    >
      {(displayScale) => loading ? null : (
        <ScaledDocumentContent
          naturalWidth={DEFAULT_DOCX_SIZE.width}
          naturalHeight={DEFAULT_DOCX_SIZE.height}
          displayScale={displayScale}
        >
          <iframe
            srcDoc={previewDocument}
            sandbox=""
            referrerPolicy="no-referrer"
            title={t("documents.wordPreviewTitle") || "Word preview"}
            className="h-full w-full border-0 bg-white"
          />
        </ScaledDocumentContent>
      )}
    </DocumentViewerFrame>
  );
});

export default WordPreview;
