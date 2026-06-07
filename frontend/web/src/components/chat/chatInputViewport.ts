const DEFAULT_TEXTAREA_MAX_HEIGHT_PX = 150;
const MOBILE_TEXTAREA_VIEWPORT_RATIO = 0.22;
const MOBILE_TEXTAREA_MIN_HEIGHT_PX = 120;
const DEFAULT_MENTION_POPUP_MAX_HEIGHT_PX = 220;
const MIN_MENTION_POPUP_MAX_HEIGHT_PX = 160;
const MENTION_POPUP_VIEWPORT_GAP_PX = 46;
const MENTION_POPUP_INPUT_GAP_PX = 6;

interface TextareaLike {
  style: {
    height: string;
  };
  scrollHeight: number;
  scrollTop: number;
}

export function resizeTextareaForContent(
  textarea: TextareaLike,
  maxHeightPx = DEFAULT_TEXTAREA_MAX_HEIGHT_PX,
): void {
  textarea.style.height = "auto";
  textarea.style.height = `${Math.min(textarea.scrollHeight, maxHeightPx)}px`;
  textarea.scrollTop = textarea.scrollHeight;
}

export function getTextareaMaxHeightPx({
  isMobile,
  viewportHeight,
}: {
  isMobile: boolean;
  viewportHeight?: number | null;
}): number {
  if (!isMobile || !viewportHeight) {
    return DEFAULT_TEXTAREA_MAX_HEIGHT_PX;
  }

  return Math.min(
    DEFAULT_TEXTAREA_MAX_HEIGHT_PX,
    Math.max(
      MOBILE_TEXTAREA_MIN_HEIGHT_PX,
      Math.round(viewportHeight * MOBILE_TEXTAREA_VIEWPORT_RATIO),
    ),
  );
}

export function getMentionPopupMaxHeightPx({
  inputTop,
  viewportHeight,
}: {
  inputTop?: number | null;
  viewportHeight?: number | null;
}): number {
  if (!inputTop || !viewportHeight) {
    return DEFAULT_MENTION_POPUP_MAX_HEIGHT_PX;
  }

  const availableHeight = Math.round(inputTop - MENTION_POPUP_VIEWPORT_GAP_PX);
  return Math.min(
    DEFAULT_MENTION_POPUP_MAX_HEIGHT_PX,
    Math.max(MIN_MENTION_POPUP_MAX_HEIGHT_PX, availableHeight),
  );
}

export function getMentionPopupFixedPlacement({
  inputRect,
  viewportHeight,
}: {
  inputRect?: Pick<DOMRect, "left" | "top" | "width"> | null;
  viewportHeight?: number | null;
}): {
  left: number;
  width: number;
  bottom: number;
  maxHeight: number;
} | null {
  if (!inputRect || !viewportHeight) {
    return null;
  }

  return {
    left: Math.round(inputRect.left),
    width: Math.round(inputRect.width),
    bottom: Math.round(
      viewportHeight - inputRect.top + MENTION_POPUP_INPUT_GAP_PX,
    ),
    maxHeight: getMentionPopupMaxHeightPx({
      inputTop: inputRect.top,
      viewportHeight,
    }),
  };
}
