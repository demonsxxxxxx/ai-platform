import { useRef, useEffect, useCallback } from "react";
import {
  getTextareaMaxHeightPx,
  resizeTextareaForContent,
} from "../components/chat/chatInputViewport";

export function useTextareaResize(
  textareaRef: React.RefObject<HTMLTextAreaElement | null>,
  input: string,
) {
  const resizeRafRef = useRef<number>(0);

  const resizeTextareaHeightNow = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    resizeTextareaForContent(
      el,
      getTextareaMaxHeightPx({
        isMobile:
          typeof window !== "undefined" ? window.innerWidth < 640 : false,
        viewportHeight:
          typeof window !== "undefined"
            ? window.visualViewport?.height ?? window.innerHeight
            : null,
      }),
    );
  }, [textareaRef]);

  const scheduleTextareaResize = useCallback(() => {
    if (typeof window === "undefined") return;
    cancelAnimationFrame(resizeRafRef.current);
    resizeRafRef.current = requestAnimationFrame(resizeTextareaHeightNow);
  }, [resizeTextareaHeightNow]);

  useEffect(() => {
    requestAnimationFrame(resizeTextareaHeightNow);
  }, [input, resizeTextareaHeightNow]);

  useEffect(() => {
    if (typeof window === "undefined") return undefined;

    const updateTextareaSize = () => scheduleTextareaResize();
    updateTextareaSize();
    window.visualViewport?.addEventListener("resize", updateTextareaSize);
    window.addEventListener("resize", updateTextareaSize);
    window.addEventListener("orientationchange", updateTextareaSize);

    return () => {
      window.visualViewport?.removeEventListener("resize", updateTextareaSize);
      window.removeEventListener("resize", updateTextareaSize);
      window.removeEventListener("orientationchange", updateTextareaSize);
    };
  }, [scheduleTextareaResize]);

  useEffect(() => {
    return () => cancelAnimationFrame(resizeRafRef.current);
  }, []);

  return { scheduleTextareaResize };
}
