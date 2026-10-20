import { memo } from "react";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { useSessionImageGallery } from "./sessionImageGallery";
import type { MarkdownHeadingAnchorContext } from "./MarkdownRenderer";

export type { MarkdownHeadingAnchorContext } from "./MarkdownRenderer";

export const MarkdownContent = memo(function MarkdownContent({
  content,
  isStreaming,
  headingAnchorContext,
}: {
  content: string;
  isStreaming?: boolean;
  headingAnchorContext?: MarkdownHeadingAnchorContext;
}) {
  const imageGallery = useSessionImageGallery();
  return (
    <MarkdownRenderer
      content={content}
      isStreaming={isStreaming}
      headingAnchorContext={headingAnchorContext}
      imageGallery={imageGallery}
    />
  );
});
