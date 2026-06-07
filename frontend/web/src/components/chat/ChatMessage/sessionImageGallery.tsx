import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { Message, MessagePart, ToolPart } from "../../../types";
import { isImageFile } from "../../documents/utils";
import { ImageViewer } from "../../common";
import { useSafeAttachmentImageSrc } from "../../common/attachmentImageSafety";
import { parseFileRevealPreviewData } from "./items/revealPreviewData";
import { resolveSafeSessionImageSrc } from "./sessionImageSafety";

export interface SessionImageGalleryItem {
  id: string;
  src: string;
  alt?: string;
  group: SessionImageGalleryGroup;
}

export type SessionImageGalleryGroup = "conversation" | "reveal-file";

interface SessionImageGalleryContextValue {
  openImage: (
    src: string,
    alt?: string,
    options?: { group?: SessionImageGalleryGroup },
  ) => void;
}

const SessionImageGalleryContext =
  createContext<SessionImageGalleryContextValue | null>(null);

function getExtension(nameOrUrl: string): string {
  const clean = nameOrUrl.split("?")[0].split("#")[0];
  return clean.split(".").pop()?.toLowerCase() || "";
}

function collectMarkdownImages(
  content: string | undefined,
  idPrefix: string,
): SessionImageGalleryItem[] {
  if (!content) return [];

  const items: SessionImageGalleryItem[] = [];
  const markdownImagePattern = /!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g;
  for (const match of content.matchAll(markdownImagePattern)) {
    const src = resolveSafeSessionImageSrc(match[2]);
    if (!src) continue;
    items.push({
      id: `${idPrefix}:image:${items.length}`,
      src,
      alt: match[1] || undefined,
      group: "conversation",
    });
  }

  const htmlImagePattern = /<img\b[^>]*\bsrc=["']([^"']+)["'][^>]*>/gi;
  for (const match of content.matchAll(htmlImagePattern)) {
    const src = resolveSafeSessionImageSrc(match[1]);
    if (!src) continue;
    items.push({
      id: `${idPrefix}:html-image:${items.length}`,
      src,
      group: "conversation",
    });
  }

  return items;
}

function collectRevealFileImage(
  part: ToolPart,
  idPrefix: string,
): SessionImageGalleryItem | null {
  if (part.name !== "reveal_file" || part.success !== true) return null;
  const parsed = parseFileRevealPreviewData({
    args: part.args,
    result: part.result,
  });
  if (!parsed.s3Url) return null;

  const isImage =
    parsed.mimeType?.startsWith("image/") === true ||
    isImageFile(getExtension(parsed.filePath || parsed.s3Url));
  if (!isImage) return null;

  return {
    id: `${idPrefix}:reveal-file`,
    src: parsed.s3Url,
    alt: parsed.filePath.split("/").pop() || undefined,
    group: "reveal-file",
  };
}

function collectPartImages(
  part: MessagePart,
  idPrefix: string,
): SessionImageGalleryItem[] {
  if (part.type === "text" || part.type === "summary") {
    return collectMarkdownImages(part.content, idPrefix);
  }

  if (part.type === "tool") {
    const revealImage = collectRevealFileImage(part, idPrefix);
    return revealImage ? [revealImage] : [];
  }

  if (part.type === "subagent") {
    return [
      ...collectMarkdownImages(part.input, `${idPrefix}:input`),
      ...collectMarkdownImages(part.result, `${idPrefix}:result`),
      ...(part.parts || []).flatMap((child, index) =>
        collectPartImages(child, `${idPrefix}:part:${index}`),
      ),
    ];
  }

  return [];
}

export function collectSessionImageGalleryItems(
  messages: Message[],
): SessionImageGalleryItem[] {
  return messages.flatMap((message) => {
    const attachmentItems = (message.attachments || []).flatMap(
      (attachment): SessionImageGalleryItem[] => {
        const isImage =
          attachment.type === "image" ||
          attachment.mimeType?.startsWith("image/");
        const src = resolveSafeSessionImageSrc(attachment.url);
        if (!isImage || !src) return [];
        return [
          {
            id: `${message.id}:attachment:${attachment.id}`,
            src,
            alt: attachment.name,
            group: "conversation",
          },
        ];
      },
    );

    const contentItems = collectMarkdownImages(
      message.content,
      `${message.id}:content`,
    );
    const partItems = (message.parts || []).flatMap((part, index) =>
      collectPartImages(part, `${message.id}:part:${index}`),
    );

    return [...attachmentItems, ...contentItems, ...partItems];
  });
}

export function useSessionImageGallery(): SessionImageGalleryContextValue | null {
  return useContext(SessionImageGalleryContext);
}

export function SessionImageGalleryProvider({
  messages,
  children,
}: {
  messages: Message[];
  children: ReactNode;
}) {
  const items = useMemo(
    () => collectSessionImageGalleryItems(messages),
    [messages],
  );
  const [activeImage, setActiveImage] =
    useState<SessionImageGalleryItem | null>(null);
  const activeIndex = activeImage
    ? items
        .filter((item) => item.group === activeImage.group)
        .findIndex((item) => item.src === activeImage.src)
    : -1;
  const activeGalleryItems = activeImage
    ? items.filter((item) => item.group === activeImage.group)
    : [];
  const currentItem =
    activeIndex >= 0 ? activeGalleryItems[activeIndex] : activeImage;
  const currentImageSrc = useSafeAttachmentImageSrc(
    currentItem?.src,
    currentItem ? "image/png" : null,
  );
  const previousItem =
    activeIndex > 0 ? activeGalleryItems[activeIndex - 1] : null;
  const nextItem =
    activeIndex >= 0 && activeIndex < activeGalleryItems.length - 1
      ? activeGalleryItems[activeIndex + 1]
      : null;
  const positionLabel =
    activeIndex >= 0 && activeGalleryItems.length > 1
      ? `${activeIndex + 1} / ${activeGalleryItems.length}`
      : undefined;

  const openImage = useCallback(
    (
      src: string,
      alt?: string,
      options?: { group?: SessionImageGalleryGroup },
    ) => {
      const resolvedSrc = resolveSafeSessionImageSrc(src);
      if (!resolvedSrc) return;
      setActiveImage({
        id: `ad-hoc:${resolvedSrc}`,
        src: resolvedSrc,
        alt,
        group: options?.group || "conversation",
      });
    },
    [],
  );

  const value = useMemo(() => ({ openImage }), [openImage]);

  return (
    <SessionImageGalleryContext.Provider value={value}>
      {children}
      {currentItem && currentImageSrc && (
        <ImageViewer
          src={currentImageSrc}
          alt={currentItem.alt || ""}
          isOpen={!!currentImageSrc}
          onClose={() => setActiveImage(null)}
          onPrevious={() => previousItem && setActiveImage(previousItem)}
          onNext={() => nextItem && setActiveImage(nextItem)}
          hasPrevious={!!previousItem}
          hasNext={!!nextItem}
          positionLabel={positionLabel}
        />
      )}
    </SessionImageGalleryContext.Provider>
  );
}
