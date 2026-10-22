import { Button, toast } from "@lobehub/ui/base-ui";
import clsx from "clsx";
import { Check, Copy, Download } from "lucide-react";
import React, { memo, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import { useTranslation } from "react-i18next";
import { DeferredCodeMirrorViewer } from "../../common/DeferredCodeMirrorViewer";
import { ImageViewer } from "../../common";
import { copyToClipboard } from "../../../utils/clipboard";
import { createHeadingAnchorId } from "../../layout/AppContent/messageOutline";
import { getFileLinkInfo } from "../../documents/utils";
import {
  isAllowedAuthenticatedArtifactFileUrl,
  isSensitiveInternalPath,
} from "../../documents/documentUrlSafety";
import { openPreviewUrl } from "../../documents/documentPreviewSources";
import { setActiveRevealPreviewState } from "./items/activeRevealPreviewStore";
import { buildFileLinkPreviewRequest } from "./items/fileLinkPreview";
import { createActiveRevealPreviewState } from "./items/revealPreviewState";
import { MermaidDiagram } from "./MermaidDiagram";
import { resolveSafeSessionImageSrc } from "./sessionImageSafety";
import { useSafeAttachmentImageSrc } from "../../common/attachmentImageSafety";

export const MARKDOWN_REMARK_PLUGINS = [remarkGfm, remarkBreaks, remarkMath];
export const MARKDOWN_REHYPE_PLUGINS = [rehypeKatex];

export interface MarkdownImageGallery {
  openImage: (src: string, alt?: string) => void;
}

export interface MarkdownHeadingAnchorContext {
  messageId: string;
  partIndex: number;
}

export interface MarkdownComponentsOptions {
  isStreaming?: boolean;
  headingAnchorContext?: MarkdownHeadingAnchorContext;
  imageGallery?: MarkdownImageGallery | null;
  primitive?: boolean;
  onOpenImage?: (src: string) => void;
}

export interface MarkdownRendererProps extends MarkdownComponentsOptions {
  content: string;
}

function extractNodeText(node: React.ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(extractNodeText).join("");
  if (React.isValidElement<{ children?: React.ReactNode }>(node)) {
    return extractNodeText(node.props.children);
  }
  return "";
}

function getHeadingAnchorId(
  children: React.ReactNode,
  headingAnchorContext?: MarkdownHeadingAnchorContext,
): string {
  const headingText = extractNodeText(children);
  return createHeadingAnchorId({
    messageId: headingAnchorContext?.messageId ?? "standalone",
    partIndex: headingAnchorContext?.partIndex ?? 0,
    headingText,
  });
}

function getCurrentOrigin(): string | undefined {
  return typeof window === "undefined" ? undefined : window.location.origin;
}

function hasAbsoluteScheme(value: string): boolean {
  return /^[a-z][a-z\d+.-]*:/i.test(value) || value.startsWith("//");
}

function isPlatformApiMarkdownLink(value: string): boolean {
  const trimmed = value.trim();
  if (trimmed.startsWith("/api/")) return true;
  const currentOrigin = getCurrentOrigin();
  if (!currentOrigin || !hasAbsoluteScheme(trimmed)) return false;
  try {
    const parsed = new URL(trimmed, currentOrigin);
    return parsed.origin === currentOrigin && parsed.pathname.startsWith("/api/");
  } catch {
    return false;
  }
}

function isUnsafeMarkdownLinkUrl(href: string): boolean {
  const trimmed = href.trim();
  if (!trimmed || isAllowedAuthenticatedArtifactFileUrl(trimmed)) return false;
  return isSensitiveInternalPath(trimmed) || isPlatformApiMarkdownLink(trimmed);
}

export function CodeHeader({ language, code }: { language?: string; code: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    if (!code || copied) return;
    await copyToClipboard(code);
    setCopied(true);
    toast.success(t("chat.message.copied"));
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <div className="flex items-center justify-between border-b border-neutral-200 bg-neutral-200/70 px-3 py-2 text-xs dark:border-neutral-700 dark:bg-neutral-800/50">
      <span className="truncate font-medium text-neutral-500 dark:text-neutral-400">{language || "text"}</span>
      <Button
        type="text"
        onClick={handleCopy}
        className={clsx(
          "flex min-h-[32px] min-w-[32px] items-center gap-1 rounded-md px-2 py-1 text-xs font-medium transition-all touch-manipulation",
          copied
            ? "text-[var(--theme-success-text)]"
            : "text-neutral-500 hover:bg-neutral-300/50 hover:text-neutral-700 dark:text-neutral-400 dark:hover:bg-neutral-700/50 dark:hover:text-neutral-200",
        )}
        title={copied ? t("chat.message.copied") : t("chat.message.copyCode")}
      >
        {copied ? <Check size={14} /> : <Copy size={14} />}
        <span className="hidden xs:inline">{copied ? t("chat.message.copied") : t("chat.message.copy")}</span>
      </Button>
    </div>
  );
}

export function CodeSyntaxHighlighter({ language, code }: { language: string; code: string }) {
  return (
    <div className="my-2 max-w-full overflow-hidden rounded-xl border border-neutral-200 dark:border-neutral-700">
      <DeferredCodeMirrorViewer
        value={code}
        language={language || undefined}
        lineNumbers
        fontSize="0.75rem"
        className="overflow-hidden rounded-b-xl [&_.cm-editor]:rounded-none [&_.cm-gutters]:border-r-0"
      />
    </div>
  );
}

function CodeBlock({
  className,
  children,
  inline,
  isInPre,
  isStreaming,
}: {
  className?: string;
  children?: React.ReactNode;
  inline?: boolean;
  isInPre?: boolean;
  isStreaming?: boolean;
}) {
  const language = /language-(\w+)/.exec(className || "")?.[1] || "";
  const codeString = String(children ?? "").replace(/\n$/, "");
  if (language === "mermaid") {
    return <MermaidDiagram chart={codeString} isStreaming={isStreaming} />;
  }
  if (inline || (!isInPre && !language)) {
    return (
      <code
        className="cursor-pointer rounded bg-neutral-200 px-1.5 py-0.5 font-mono text-sm text-neutral-800 transition-colors hover:bg-neutral-300 dark:bg-neutral-700 dark:text-neutral-200 dark:hover:bg-neutral-600"
        onClick={() => {
          void copyToClipboard(String(children));
        }}
      >
        {children}
      </code>
    );
  }
  return (
    <div className="my-2 max-w-full overflow-hidden rounded-xl border border-neutral-200 dark:border-neutral-700">
      <CodeHeader language={language} code={codeString} />
      <CodeSyntaxHighlighter language={language} code={codeString} />
    </div>
  );
}

function TableBlock({ children }: { children: React.ReactNode }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const tableRef = React.useRef<HTMLTableElement>(null);
  const extractData = () => {
    if (!tableRef.current) return [] as string[][];
    return Array.from(tableRef.current.querySelectorAll("tr")).map((row) =>
      Array.from(row.querySelectorAll("th, td")).map((cell) => cell.textContent?.trim() || ""),
    );
  };
  const handleCopy = async () => {
    const data = extractData();
    if (!data.length) return;
    const widths = data[0].map((_, index) => Math.max(...data.map((row) => (row[index] || "").length)));
    const pad = (value: string, width: number) => value.padEnd(width);
    const lines = [
      `| ${data[0].map((value, index) => pad(value, widths[index])).join(" | ")} |`,
      `| ${widths.map((width) => "-".repeat(width)).join(" | ")} |`,
      ...data.slice(1).map((row) => `| ${row.map((value, index) => pad(value, widths[index])).join(" | ")} |`),
    ];
    await copyToClipboard(lines.join("\n"));
    setCopied(true);
    toast.success(t("chat.message.copied"));
    setTimeout(() => setCopied(false), 2000);
  };
  const handleExport = () => {
    const csv = extractData().map((row) => row.map((cell) => `"${cell.replace(/"/g, '""')}"`).join(",")).join("\r\n");
    const url = URL.createObjectURL(new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8;" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `table-${Date.now()}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };
  return (
    <div className="group/table relative my-3 overflow-hidden rounded-lg shadow ring-1 ring-neutral-200 dark:ring-neutral-700">
      <div className="absolute right-1.5 top-1.5 z-20 flex items-center gap-0.5 rounded-md border border-neutral-200/50 bg-neutral-100/90 px-1 py-0.5 shadow-sm dark:border-neutral-700/50 dark:bg-neutral-800/90 sm:opacity-0 sm:transition-opacity sm:group-hover/table:opacity-100">
        <Button type="text" onClick={handleCopy} title={copied ? t("chat.message.copied") : t("chat.message.copy")} className="p-1.5 text-neutral-500">
          {copied ? <Check size={13} /> : <Copy size={13} />}
        </Button>
        <Button type="text" onClick={handleExport} title={t("chat.message.exportCsv", "Export CSV")} className="p-1.5 text-neutral-500">
          <Download size={13} />
        </Button>
      </div>
      <div className="overflow-x-auto">
        <table ref={tableRef} className="min-w-full divide-y divide-neutral-200 dark:divide-neutral-700">{children}</table>
      </div>
    </div>
  );
}

function SafeMarkdownImage({
  src,
  alt,
  imageGallery,
  onOpen,
}: {
  src?: string;
  alt?: string;
  imageGallery?: MarkdownImageGallery | null;
  onOpen: (src: string) => void;
}) {
  const safeSrc = resolveSafeSessionImageSrc(src);
  const imageSrc = useSafeAttachmentImageSrc(safeSrc, "image/png");
  if (!safeSrc || !imageSrc) return null;
  return (
    <img
      src={imageSrc}
      alt={alt}
      loading="lazy"
      className="my-2 h-auto max-w-full cursor-zoom-in rounded-lg shadow transition-opacity hover:opacity-90"
      onClick={() => {
        if (imageGallery) imageGallery.openImage(safeSrc, alt || undefined);
        else onOpen(imageSrc);
      }}
    />
  );
}

export function createMarkdownComponents(options: MarkdownComponentsOptions = {}): Components {
  const { headingAnchorContext, imageGallery, isStreaming, onOpenImage, primitive = false } = options;
  const heading = (Tag: "h1" | "h2" | "h3" | "h4", className: string) => ({ children }: { children?: React.ReactNode }) => {
    const id = getHeadingAnchorId(children, headingAnchorContext);
    return <Tag id={id} data-outline-anchor="true" data-outline-id={id} className={className}><a href={`#${id}`} className="text-inherit no-underline transition-colors hover:text-[var(--theme-warning-text)]">{children}</a></Tag>;
  };
  const components: Components = {
    h1: heading("h1", "mt-3 mb-2 scroll-mt-4 text-xl font-bold text-neutral-900 first:mt-0 dark:text-neutral-100"),
    h2: heading("h2", "mt-3 mb-1.5 scroll-mt-4 text-lg font-bold text-neutral-900 dark:text-neutral-100"),
    h3: heading("h3", "mt-2 mb-1 scroll-mt-4 text-base font-semibold text-neutral-900 dark:text-neutral-100"),
    h4: heading("h4", "mt-2 mb-1 scroll-mt-4 text-base font-semibold text-neutral-800 dark:text-neutral-200"),
    p: ({ children }) => <p className="mb-2 text-[15px] leading-6 text-neutral-700 last:mb-0 dark:text-neutral-300">{children}</p>,
    ul: ({ children }) => <ul className="mb-2 list-disc space-y-1 pl-5 marker:text-[var(--theme-warning-text)]">{children}</ul>,
    ol: ({ children }) => <ol className="mb-2 list-inside list-decimal space-y-1 pl-5 marker:font-semibold marker:text-neutral-500">{children}</ol>,
    li: ({ children }) => <li className="text-[15px] leading-6 text-neutral-700 dark:text-neutral-300">{children}</li>,
    blockquote: ({ children }) => <blockquote className="my-3 rounded-r-lg border-l-4 border-[var(--theme-warning)] bg-[var(--theme-warning-soft)] px-3 py-2"><div className="text-sm text-neutral-600 dark:text-neutral-300">{children}</div></blockquote>,
    a: ({ href, children }) => {
      if (href && isAllowedAuthenticatedArtifactFileUrl(href)) {
        return <Button type="text" htmlType="button" className="cursor-pointer bg-transparent p-0 align-baseline text-[var(--theme-info-text)] hover:underline" onClick={(event) => { event.preventDefault(); event.stopPropagation(); void openPreviewUrl({ url: href }); }}>{children}</Button>;
      }
      if (href && isUnsafeMarkdownLinkUrl(href)) return <span className="text-neutral-700 dark:text-neutral-300">{children}</span>;
      if (href) {
        const fileLinkInfo = getFileLinkInfo(href, extractNodeText(children));
        if (fileLinkInfo.isFile) {
          const previewRequest = buildFileLinkPreviewRequest({ href, fileName: fileLinkInfo.fileName });
          if (previewRequest) return <a href={href} className="text-[var(--theme-info-text)] hover:underline" onClick={(event) => { event.preventDefault(); event.stopPropagation(); setActiveRevealPreviewState(createActiveRevealPreviewState(previewRequest, "manual")); }}>{children}</a>;
        }
      }
      return <a href={href} target="_blank" rel="noopener noreferrer" className="text-[var(--theme-info-text)] hover:underline">{children}</a>;
    },
    hr: () => <hr className="my-4 h-px border-0 bg-neutral-300 dark:bg-neutral-600" />,
    strong: ({ children }) => <strong className="font-bold text-neutral-900 dark:text-neutral-100">{children}</strong>,
    em: ({ children }) => <em className="italic text-neutral-600 dark:text-neutral-400">{children}</em>,
    table: ({ children }) => <TableBlock>{children}</TableBlock>,
    thead: ({ children }) => <thead className="bg-neutral-50 dark:bg-neutral-800">{children}</thead>,
    tbody: ({ children }) => <tbody className="divide-y divide-neutral-200 bg-[var(--theme-bg-card)] dark:divide-neutral-700 dark:bg-neutral-900">{children}</tbody>,
    tr: ({ children }) => <tr className="transition-colors hover:bg-neutral-50 dark:hover:bg-neutral-800/50">{children}</tr>,
    th: ({ children }) => <th scope="col" className="min-w-[60px] whitespace-nowrap px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider text-neutral-700 dark:text-neutral-300">{children}</th>,
    td: ({ children }) => <td className="min-w-[60px] break-words px-3 py-2 text-sm text-neutral-600 dark:text-neutral-400">{children}</td>,
    img: ({ src, alt }) => <SafeMarkdownImage src={src} alt={alt} imageGallery={imageGallery} onOpen={onOpenImage ?? (() => undefined)} />,
  };
  if (primitive) {
    components.code = ({ children, ...props }) => <code {...props} className="rounded bg-neutral-200 px-1.5 py-0.5 font-mono text-sm text-neutral-800 dark:bg-neutral-700 dark:text-neutral-200">{children}</code>;
  } else {
    components.code = (props) => <CodeBlock {...(props as React.ComponentProps<"code"> & { isInPre?: boolean })} isStreaming={isStreaming} />;
    components.pre = ({ children }) => React.isValidElement(children) ? React.cloneElement(children as React.ReactElement<{ isInPre?: boolean }>, { isInPre: true }) : <>{children}</>;
  }
  return components;
}

export const MarkdownRenderer = memo(function MarkdownRenderer({ content, isStreaming, headingAnchorContext, imageGallery }: MarkdownRendererProps) {
  const [imageViewerSrc, setImageViewerSrc] = useState<string | null>(null);
  const components = React.useMemo(() => createMarkdownComponents({ isStreaming, headingAnchorContext, imageGallery, onOpenImage: setImageViewerSrc }), [headingAnchorContext, imageGallery, isStreaming]);
  return (
    <span className="markdown-preview my-1 block">
      <ReactMarkdown remarkPlugins={MARKDOWN_REMARK_PLUGINS} rehypePlugins={MARKDOWN_REHYPE_PLUGINS} components={{ ...components, img: ({ src, alt }) => <SafeMarkdownImage src={src} alt={alt} imageGallery={imageGallery} onOpen={setImageViewerSrc} /> }}>
        {content}
      </ReactMarkdown>
      {!imageGallery && <ImageViewer src={imageViewerSrc || ""} isOpen={!!imageViewerSrc} onClose={() => setImageViewerSrc(null)} />}
    </span>
  );
});
