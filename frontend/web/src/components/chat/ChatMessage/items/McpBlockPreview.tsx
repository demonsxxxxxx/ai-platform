import {
  ChevronDown,
  ChevronUp,
  ExternalLink,
  FileText,
  Image as ImageIcon,
  File,
} from "lucide-react";
import { useState, useEffect, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import { createPortal } from "react-dom";
import { MarkdownContent } from "../MarkdownContent";
import { CopyButton } from "../../../common";
import { dispatchPersonaPresetsChanged } from "../../../../hooks/personaPresetEvents";
import { getPersonaPresetMutationDetail } from "./personaPresetToolResult";
import type { McpContentBlock, McpMultiModalResult } from "./toolUtils";
import { isMarkdownText, extractText } from "./toolUtils";
import { ToolResultPanel } from "./ToolResultPanel";
import {
  closeBlockPreview,
  getBlockPreview,
  openBlockPreview,
  subscribeBlockPreview,
} from "./blockPreviewStore";
import {
  isUnsafeExternalHttpDocumentUrl,
  isUnsafeUnauthenticatedDocumentUrl,
} from "../../../documents/documentFetchCache";
import {
  openPreviewUrl,
  resolveDocumentPreviewUrl,
} from "../../../documents/documentPreviewSources";
import { isSensitiveInternalPath } from "../../../documents/documentUrlSafety";

function useBlockPreview() {
  const [, setCount] = useState(0);
  useEffect(() => {
    const fn = () => setCount((c) => c + 1);
    return subscribeBlockPreview(fn);
  }, []);
  return { preview: getBlockPreview(), close: closeBlockPreview };
}

function normalizePreviewUrl(url: string | undefined | null): string {
  return typeof url === "string" ? url.trim() : "";
}

function isInlineImageSrc(src: string): boolean {
  return src.startsWith("blob:") || /^data:image\//i.test(src);
}

function revokeBlobUrl(url: string): void {
  if (url.startsWith("blob:") && typeof URL !== "undefined") {
    URL.revokeObjectURL(url);
  }
}

function canOpenPreviewUrl(url: string): boolean {
  const normalizedUrl = normalizePreviewUrl(url);
  return (
    !!normalizedUrl &&
    !isSensitiveInternalPath(normalizedUrl) &&
    !isUnsafeExternalHttpDocumentUrl(normalizedUrl) &&
    !isUnsafeUnauthenticatedDocumentUrl(normalizedUrl)
  );
}

function getPreviewFileName(url: string, fallback: string): string {
  const path = normalizePreviewUrl(url).split(/[?#]/)[0] || "";
  const lastSegment = path.split("/").filter(Boolean).pop();
  if (!lastSegment) return fallback;

  try {
    return decodeURIComponent(lastSegment) || fallback;
  } catch {
    return lastSegment;
  }
}

function useResolvedPreviewUrl(
  url: string,
  mimeType?: string | null,
): { safeUrl: string; resolving: boolean } {
  const [safeUrl, setSafeUrl] = useState("");
  const [resolving, setResolving] = useState(false);

  useEffect(() => {
    let active = true;
    let objectUrl = "";
    const normalizedUrl = normalizePreviewUrl(url);

    setSafeUrl("");
    setResolving(false);

    if (!normalizedUrl) {
      return () => {
        active = false;
      };
    }

    if (isInlineImageSrc(normalizedUrl)) {
      setSafeUrl(normalizedUrl);
      return () => {
        active = false;
      };
    }

    setResolving(true);
    void resolveDocumentPreviewUrl({
      url: normalizedUrl,
      mimeType,
    })
      .then((resolvedUrl) => {
        if (resolvedUrl.startsWith("blob:")) {
          objectUrl = resolvedUrl;
        }
        if (!active) {
          if (objectUrl) revokeBlobUrl(objectUrl);
          return;
        }
        setSafeUrl(resolvedUrl);
      })
      .catch(() => {
        if (active) {
          setSafeUrl("");
        }
      })
      .finally(() => {
        if (active) {
          setResolving(false);
        }
      });

    return () => {
      active = false;
      if (objectUrl) revokeBlobUrl(objectUrl);
    };
  }, [mimeType, url]);

  return { safeUrl, resolving };
}

function PortalImagePreview({
  src,
  alt,
}: {
  src: string;
  alt: string;
}) {
  const { safeUrl, resolving } = useResolvedPreviewUrl(src);

  if (!safeUrl && !resolving) return null;

  return (
    <div className="flex items-center justify-center p-4 bg-stone-50 dark:bg-stone-900 min-h-[200px]">
      {safeUrl ? (
        <img
          src={safeUrl}
          alt={alt}
          className="max-w-full max-h-[70vh] object-contain rounded-lg"
        />
      ) : (
        <div className="w-48 h-32 rounded-md border border-stone-200 dark:border-stone-700 bg-stone-100 dark:bg-stone-800 animate-pulse" />
      )}
    </div>
  );
}

function FilePreviewContent({
  url,
  fileName,
}: {
  url: string;
  fileName: string;
}) {
  const { t } = useTranslation();
  const normalizedUrl = normalizePreviewUrl(url);
  const canOpen = canOpenPreviewUrl(normalizedUrl);

  return (
    <div className="p-4 sm:p-5 space-y-3">
      <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-stone-100 dark:bg-stone-800 text-sm text-stone-500 dark:text-stone-400 font-mono truncate">
        <span className="truncate">{fileName}</span>
      </div>
      {canOpen && (
        <button
          type="button"
          onClick={() => void openPreviewUrl({ url: normalizedUrl })}
          className="inline-flex items-center gap-2 px-4 py-2.5 rounded-lg bg-stone-100 dark:bg-stone-800 text-sm text-stone-600 dark:text-stone-300 hover:bg-stone-200 dark:hover:bg-stone-700 transition-colors border border-stone-200 dark:border-stone-700"
        >
          <ExternalLink size={14} />
          {t("chat.message.toolOpenFile", "Open file")}
        </button>
      )}
    </div>
  );
}

function McpImageBlockPreview({ block }: { block: McpContentBlock }) {
  const { t } = useTranslation();
  const [loaded, setLoaded] = useState(false);
  const rawSrc = block.base64
    ? `data:${block.mime_type || "image/png"};base64,${block.base64}`
    : normalizePreviewUrl(block.url);
  const { safeUrl, resolving } = useResolvedPreviewUrl(
    rawSrc,
    block.mime_type || "image/png",
  );

  useEffect(() => {
    setLoaded(false);
  }, [safeUrl]);

  if (!rawSrc || (!safeUrl && !resolving)) return null;

  return (
    <>
      {!loaded && (
        <div className="w-48 h-32 rounded-md border border-stone-200 dark:border-stone-700 bg-stone-100 dark:bg-stone-800 animate-pulse" />
      )}
      {safeUrl && (
        <img
          src={safeUrl}
          alt={t("chat.message.toolOutput")}
          className={`max-w-full max-h-48 rounded-md border border-stone-200 dark:border-stone-700 cursor-pointer hover:opacity-80 transition-opacity${
            !loaded ? " hidden" : ""
          }`}
          onClick={() => openBlockPreview({ type: "image", src: safeUrl })}
          onLoad={() => setLoaded(true)}
        />
      )}
    </>
  );
}

function McpFileBlockPreview({ block }: { block: McpContentBlock }) {
  const { t } = useTranslation();
  const url = normalizePreviewUrl(block.url);
  const fileName = getPreviewFileName(url, t("chat.message.toolFile"));
  const canOpen = canOpenPreviewUrl(url);

  return (
    <button
      type="button"
      disabled={!canOpen}
      onClick={() => {
        if (canOpen) openBlockPreview({ type: "file", url, fileName });
      }}
      className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-md bg-stone-100 dark:bg-stone-800 text-xs text-stone-600 dark:text-stone-300 transition-colors border border-stone-200 dark:border-stone-700${
        canOpen
          ? " hover:bg-stone-200 dark:hover:bg-stone-700 cursor-pointer"
          : " opacity-60 cursor-default"
      }`}
    >
      <File size={12} />
      {fileName}
    </button>
  );
}

function RichResultLink({ title, url }: { title: string; url: string }) {
  const normalizedUrl = normalizePreviewUrl(url);
  const canOpen = canOpenPreviewUrl(normalizedUrl);
  const label = title || getPreviewFileName(normalizedUrl, normalizedUrl);

  if (!canOpen) {
    return (
      <span className="text-xs font-medium text-stone-700 dark:text-stone-200 truncate">
        {label}
      </span>
    );
  }

  return (
    <button
      type="button"
      onClick={() => void openPreviewUrl({ url: normalizedUrl })}
      className="text-xs font-medium text-stone-700 dark:text-stone-200 hover:underline truncate text-left"
    >
      {label}
    </button>
  );
}

/** Standalone portal — render once at app level, survives any component tree changes */
export function BlockPreviewPortal() {
  const { t } = useTranslation();
  const { preview, close } = useBlockPreview();

  if (!preview) return null;

  let icon: React.ReactNode;
  let title: string;
  let content: React.ReactNode;

  if (preview.type === "image" && preview.src) {
    icon = <ImageIcon size={16} />;
    title = t("chat.message.toolOutput");
    content = (
      <PortalImagePreview
        src={preview.src}
        alt={t("chat.message.toolOutput")}
      />
    );
  } else if (preview.type === "file" && preview.url) {
    icon = <File size={16} />;
    title = preview.fileName || t("chat.message.toolFile");
    content = (
      <FilePreviewContent url={preview.url} fileName={title} />
    );
  } else if (preview.type === "text" && preview.text) {
    icon = <FileText size={16} />;
    title = t("chat.message.toolOutput");
    content = (
      <div className="p-4 sm:p-5">
        <div className="flex justify-end mb-2">
          <CopyButton text={preview.text} />
        </div>
        <pre className="text-sm text-stone-700 dark:text-stone-300 whitespace-pre-wrap break-words font-mono">
          {preview.text}
        </pre>
      </div>
    );
  } else {
    return null;
  }

  return createPortal(
    <ToolResultPanel
      open
      onClose={close}
      title={title}
      icon={icon}
      status="success"
    >
      {content}
    </ToolResultPanel>,
    document.body,
  );
}

// LangChain content blocks 数组: [{"type": "text", "text": "..."}, ...]
function isContentBlocksArray(result: unknown): result is McpContentBlock[] {
  return (
    Array.isArray(result) &&
    result.length > 0 &&
    typeof result[0] === "object" &&
    result[0] !== null &&
    "type" in result[0]
  );
}

// 单个 MCP content block 的预览
export function McpBlockPreview({ block }: { block: McpContentBlock }) {
  if (block.type === "image") {
    return <McpImageBlockPreview block={block} />;
  }

  if (block.type === "file") {
    return <McpFileBlockPreview block={block} />;
  }

  if (block.text) {
    return (
      <div className="group/pre relative">
        <pre
          onClick={() => openBlockPreview({ type: "text", text: block.text })}
          className="text-xs text-stone-600 dark:text-stone-300 whitespace-pre-wrap break-words overflow-y-auto min-w-0 cursor-pointer hover:text-stone-900 dark:hover:text-stone-100 transition-colors"
        >
          {block.text}
        </pre>
        <div className="absolute top-0.5 right-0.5 opacity-0 group-hover/pre:opacity-100 transition-opacity">
          <CopyButton text={block.text} size={12} />
        </div>
      </div>
    );
  }

  return null;
}

// 工具结果渲染组件 — 支持 str / dict / MCP 多模态
export function ToolResultContent({
  result,
}: {
  result?: string | Record<string, unknown>;
}) {
  const personaMutationDetail = useMemo(
    () => getPersonaPresetMutationDetail(result),
    [result],
  );
  const lastPersonaMutationKeyRef = useRef<string | null>(null);

  useEffect(() => {
    if (!personaMutationDetail) return;
    const key = JSON.stringify(personaMutationDetail);
    if (lastPersonaMutationKeyRef.current === key) return;
    lastPersonaMutationKeyRef.current = key;
    dispatchPersonaPresetsChanged(personaMutationDetail);
  }, [personaMutationDetail]);

  const textContent = extractText(result);

  // LangChain content blocks 数组: [{"type": "text", "text": "..."}, ...]
  if (isContentBlocksArray(result)) {
    const blocks = result as McpContentBlock[];
    const textParts: string[] = [];
    const mediaBlocks: McpContentBlock[] = [];

    for (const block of blocks) {
      if (block.type === "text" && block.text) {
        textParts.push(block.text);
      } else if (block.type === "image" || block.type === "file") {
        mediaBlocks.push(block);
      }
    }

    const combinedText = textParts.join("\n");
    return (
      <div className="space-y-1.5">
        {combinedText && (
          <div className="group/result relative text-xs text-stone-600 dark:text-stone-300 overflow-y-auto">
            {isMarkdownText(combinedText) ? (
              <MarkdownContent content={combinedText} />
            ) : (
              combinedText
            )}
            <div className="absolute top-0.5 right-0.5 opacity-0 group-hover/result:opacity-100 transition-opacity">
              <CopyButton text={combinedText} size={12} />
            </div>
          </div>
        )}
        {mediaBlocks.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {mediaBlocks.map((block, i) => (
              <McpBlockPreview key={i} block={block} />
            ))}
          </div>
        )}
      </div>
    );
  }

  if (
    typeof result === "object" &&
    result !== null &&
    "blocks" in result &&
    Array.isArray((result as McpMultiModalResult).blocks)
  ) {
    const mcp = result as McpMultiModalResult;
    return (
      <div className="space-y-1.5">
        {mcp.text &&
          (isMarkdownText(mcp.text) ? (
            <div className="group/result relative text-xs text-stone-600 dark:text-stone-300 overflow-y-auto">
              <MarkdownContent content={mcp.text} />
              <div className="absolute top-0.5 right-0.5 opacity-0 group-hover/result:opacity-100 transition-opacity">
                <CopyButton text={mcp.text} size={12} />
              </div>
            </div>
          ) : (
            <pre className="group/result relative text-xs text-stone-600 dark:text-stone-300 whitespace-pre-wrap break-words max-h-64 overflow-y-auto">
              {mcp.text}
              <div className="absolute top-0.5 right-0.5 opacity-0 group-hover/result:opacity-100 transition-opacity">
                <CopyButton text={mcp.text} size={12} />
              </div>
            </pre>
          ))}
        <div className="flex flex-wrap gap-2">
          {(mcp.blocks || []).map((block, i) => (
            <McpBlockPreview key={i} block={block} />
          ))}
        </div>
      </div>
    );
  }

  // 富文本结果：dict 含 title/url/content 结构
  if (
    typeof result === "object" &&
    result !== null &&
    typeof result.content === "string" &&
    (typeof result.title === "string" || typeof result.url === "string")
  ) {
    const title = typeof result.title === "string" ? result.title : "";
    const url = typeof result.url === "string" ? result.url : "";
    return (
      <div className="rounded-md border border-stone-200 dark:border-stone-700 overflow-hidden">
        {(title || url) && (
          <div className="flex items-center gap-2 px-3 py-2 bg-stone-100 dark:bg-stone-800 border-b border-stone-200 dark:border-stone-700">
            <FileText
              size={14}
              className="shrink-0 text-stone-500 dark:text-stone-400"
            />
            {url ? (
              <RichResultLink title={title} url={url} />
            ) : (
              <span className="text-xs font-medium text-stone-700 dark:text-stone-200 truncate">
                {title}
              </span>
            )}
            {url && canOpenPreviewUrl(url) && (
              <ExternalLink
                size={12}
                className="shrink-0 text-stone-400 dark:text-stone-500 ml-auto"
              />
            )}
          </div>
        )}
        <div className="group/rich relative p-3 text-xs text-stone-600 dark:text-stone-300 max-h-96 overflow-y-auto">
          <MarkdownContent content={result.content} />
          <div className="absolute top-1 right-1 opacity-0 group-hover/rich:opacity-100 transition-opacity">
            <CopyButton text={result.content} size={12} />
          </div>
        </div>
      </div>
    );
  }

  // Plain object or JSON-parseable string — render as JSON
  if (typeof result === "object" && result !== null) {
    return <JsonFallback data={result} />;
  }

  if (textContent && typeof result === "string") {
    try {
      const parsed = JSON.parse(textContent);
      if (typeof parsed === "object" && parsed !== null) {
        return <JsonFallback data={parsed} />;
      }
    } catch {
      // not JSON, fall through
    }
  }

  if (textContent) {
    return isMarkdownText(textContent) ? (
      <div className="group/result relative text-xs text-stone-600 dark:text-stone-300 overflow-y-auto">
        <MarkdownContent content={textContent} />
        <div className="absolute top-0.5 right-0.5 opacity-0 group-hover/result:opacity-100 transition-opacity">
          <CopyButton text={textContent} size={12} />
        </div>
      </div>
    ) : (
      <pre className="group/result relative text-xs text-stone-600 dark:text-stone-300 overflow-y-auto whitespace-pre-wrap break-words">
        {textContent}
        <div className="absolute top-0.5 right-0.5 opacity-0 group-hover/result:opacity-100 transition-opacity">
          <CopyButton text={textContent} size={12} />
        </div>
      </pre>
    );
  }

  return <JsonFallback data={result} />;
}

const MAX_JSON_COLLAPSED = 640;

function JsonFallback({ data }: { data: unknown }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const str = JSON.stringify(data, null, 2);
  const needsTruncation = str.length > MAX_JSON_COLLAPSED;
  const display =
    needsTruncation && !expanded
      ? str.slice(0, MAX_JSON_COLLAPSED) + "\n…"
      : str;

  return (
    <div className="group/json relative">
      <div className="absolute top-1 right-1 opacity-0 group-hover/json:opacity-100 transition-opacity z-10">
        <CopyButton text={str} size={12} />
      </div>
      <pre className="text-xs text-stone-600 dark:text-stone-300 overflow-y-auto whitespace-pre-wrap break-words min-w-0">
        {display}
      </pre>
      {needsTruncation && (
        <button
          onClick={() => setExpanded((v) => !v)}
          className="flex items-center gap-1 mt-1 text-xs text-stone-500 dark:text-stone-400 hover:text-stone-700 dark:hover:text-stone-200 transition-colors"
        >
          {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          {expanded ? t("chat.message.collapse") : t("chat.message.expandAll")}
        </button>
      )}
    </div>
  );
}
