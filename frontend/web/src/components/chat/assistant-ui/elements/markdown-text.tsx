import {
  MarkdownTextPrimitive,
  memoizeMarkdownComponents,
  type MarkdownTextPrimitiveProps,
} from "@assistant-ui/react-markdown";
import { useMemo, useState } from "react";
import remend from "remend";
import {
  CodeHeader,
  CodeSyntaxHighlighter,
  createMarkdownComponents,
  MARKDOWN_REHYPE_PLUGINS,
  MARKDOWN_REMARK_PLUGINS,
  type MarkdownComponentsOptions,
  type MarkdownHeadingAnchorContext,
  type MarkdownImageGallery,
} from "../../ChatMessage/MarkdownRenderer";
import { ImageViewer } from "../../../common";
import { MermaidDiagram } from "../../ChatMessage/MermaidDiagram";

// Complete only emphasis in the displayed prefix. Links, images, code, math
// and raw HTML keep their existing parsing and authorization behavior.
function completeStreamingEmphasis(text: string): string {
  return remend(text, {
    bold: true,
    italic: true,
    boldItalic: true,
    strikethrough: true,
    links: false,
    images: false,
    inlineCode: false,
    katex: false,
    inlineKatex: false,
    setextHeadings: false,
    singleTilde: false,
    comparisonOperators: false,
    htmlTags: false,
  });
}

export interface AssistantMarkdownTextProps
  extends Omit<MarkdownComponentsOptions, "primitive"> {
  smooth?: boolean;
  defer?: boolean;
  /** Show the streaming caret after the last rendered block. */
  streamingCaret?: boolean;
  /** Mark where a stopped answer was cut off. */
  stoppedCaret?: boolean;
  containerProps?: MarkdownTextPrimitiveProps["containerProps"];
}

export function AssistantMarkdownText({
  smooth = false,
  defer = true,
  streamingCaret = false,
  stoppedCaret = false,
  headingAnchorContext,
  imageGallery,
  isStreaming,
  containerProps,
}: AssistantMarkdownTextProps) {
  const [imageViewerSrc, setImageViewerSrc] = useState<string | null>(null);
  const mermaidSyntaxHighlighter = useMemo(() => {
    function MermaidLanguageBlock({ code }: { code: string }) {
      return <MermaidDiagram chart={code} isStreaming={isStreaming} />;
    }
    MermaidLanguageBlock.displayName = "MermaidLanguageBlock";
    return MermaidLanguageBlock;
  }, [isStreaming]);
  const mermaidCodeHeader = useMemo(() => {
    function MermaidCodeHeader() {
      return null;
    }
    MermaidCodeHeader.displayName = "MermaidCodeHeader";
    return MermaidCodeHeader;
  }, []);
  const components = useMemo(
    () =>
      memoizeMarkdownComponents(
        createMarkdownComponents({
          primitive: true,
          headingAnchorContext,
          imageGallery,
          isStreaming,
          onOpenImage: setImageViewerSrc,
        }) as Parameters<typeof memoizeMarkdownComponents>[0],
      ),
    [headingAnchorContext, imageGallery, isStreaming],
  );
  return (
    <>
      <MarkdownTextPrimitive
        remarkPlugins={MARKDOWN_REMARK_PLUGINS}
        rehypePlugins={MARKDOWN_REHYPE_PLUGINS}
        preprocess={smooth ? completeStreamingEmphasis : undefined}
        components={{
          ...components,
          CodeHeader,
          SyntaxHighlighter: CodeSyntaxHighlighter,
        }}
        componentsByLanguage={{
          mermaid: {
            CodeHeader: mermaidCodeHeader,
            SyntaxHighlighter: mermaidSyntaxHighlighter,
          },
        }}
        className={streamingCaret ? "markdown-preview markdown-streaming my-1 block" : stoppedCaret ? "markdown-preview markdown-stopped my-1 block" : "markdown-preview my-1 block"}
        containerProps={containerProps}
        smooth={smooth}
        defer={defer}
      />
      {!imageGallery && (
        <ImageViewer
          src={imageViewerSrc || ""}
          isOpen={!!imageViewerSrc}
          onClose={() => setImageViewerSrc(null)}
        />
      )}
    </>
  );
}

export type {
  MarkdownHeadingAnchorContext,
  MarkdownImageGallery,
};
