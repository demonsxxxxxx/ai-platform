// Adapted from assistant-ui Elements follow-up-suggestions.aui.tsx at 727a02dc (MIT).
import { AuiIf, ThreadPrimitive, useAuiState } from "@assistant-ui/react";
import { Button } from "@lobehub/ui/base-ui";
import { useCallback, useEffect, useRef, useState, type FC } from "react";
import { useTranslation } from "react-i18next";

/** One horizontally scrollable row with edge fades; the Virtuoso viewport stays the scroll owner. */
const FollowupSuggestionsRow: FC = () => {
  const suggestions = useAuiState((state) => state.thread.suggestions);
  const { t } = useTranslation();
  const scrollRef = useRef<HTMLDivElement>(null);
  const rtlRef = useRef<boolean | null>(null);
  const [fades, setFades] = useState({ left: false, right: false });

  const updateFades = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const maxScroll = el.scrollWidth - el.clientWidth;
    // scrollLeft runs 0..-max in RTL; normalize to hidden width per physical edge.
    const fromStart = Math.abs(el.scrollLeft);
    // getComputedStyle forces a style recalc per scroll event; direction is stable.
    const rtl = (rtlRef.current ??= getComputedStyle(el).direction === "rtl");
    const [left, right] = rtl
      ? [maxScroll - fromStart, fromStart]
      : [fromStart, maxScroll - fromStart];
    setFades((previous) => {
      const next = { left: left > 1, right: right > 1 };
      return previous.left === next.left && previous.right === next.right ? previous : next;
    });
  }, []);

  useEffect(() => {
    updateFades();
    const el = scrollRef.current;
    if (!el?.firstElementChild) return undefined;
    const observer = new ResizeObserver(updateFades);
    observer.observe(el);
    observer.observe(el.firstElementChild);
    return () => observer.disconnect();
  }, [updateFades]);

  const maskImage = `linear-gradient(to right, ${
    fades.left ? "transparent, black 2rem" : "black"
  }, ${fades.right ? "black calc(100% - 2rem), transparent" : "black"})`;

  return (
    <div
      ref={scrollRef}
      onScroll={updateFades}
      data-follow-up-suggestions
      aria-label={t("chat.followUpSuggestions")}
      className="w-full overflow-x-auto [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden"
      style={{ maskImage, WebkitMaskImage: maskImage }}
    >
      <div className="mx-auto flex min-h-8 w-max items-center gap-2 px-0.5">
        {suggestions.map((suggestion, index) => (
          <ThreadPrimitive.Suggestion key={index} prompt={suggestion.prompt} send asChild>
            <Button
              type="default"
              htmlType="button"
              data-follow-up-suggestion
              style={{ height: "auto", minHeight: 40, padding: "6px 10px", whiteSpace: "nowrap", fontSize: 13 }}
            >
              {suggestion.title ?? suggestion.prompt}
              {suggestion.label ? (
                <span className="ms-1 text-[var(--theme-text-secondary)]">{suggestion.label}</span>
              ) : null}
            </Button>
          </ThreadPrimitive.Suggestion>
        ))}
      </div>
    </div>
  );
};

/** Shows only model-generated suggestions for a settled, non-empty thread. */
export const ThreadFollowupSuggestions: FC = () => (
  <AuiIf
    condition={(state) =>
      !state.thread.isEmpty &&
      !state.thread.isRunning &&
      state.thread.suggestions.length > 0
    }
  >
    <FollowupSuggestionsRow />
  </AuiIf>
);
