import {
  MarkdownTextPrimitive,
  memoizeMarkdownComponents,
  type MarkdownTextPrimitiveProps,
} from "@assistant-ui/react-markdown";
import { useMemo, useState } from "react";
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

export interface AssistantMarkdownTextProps
  extends Omit<MarkdownComponentsOptions, "primitive"> {
  smooth?: boolean;
  defer?: boolean;
  containerProps?: MarkdownTextPrimitiveProps["containerProps"];
}

export function AssistantMarkdownText({
  smooth = false,
  defer = true,
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
        className="markdown-preview my-1 block"
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
