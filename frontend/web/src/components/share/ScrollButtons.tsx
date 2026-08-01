import { ArrowDown, ArrowUp } from "lucide-react";
import { useTranslation } from "react-i18next";

interface ScrollButtonsProps {
  showTop: boolean;
  showBottom: boolean;
  onScrollToTop: () => void;
  onScrollToBottom: () => void;
}

export function ScrollButtons({
  showTop,
  showBottom,
  onScrollToTop,
  onScrollToBottom,
}: ScrollButtonsProps) {
  const { t } = useTranslation();

  const buttonClass =
    "landing-scroll-btn flex h-10 w-10 items-center justify-center rounded-xl border border-stone-200/60 bg-white/90 text-stone-400 shadow-lg shadow-stone-200/30 transition-all duration-300 hover:-translate-y-0.5 hover:bg-white hover:text-stone-700 hover:shadow-xl dark:border-stone-700/40 dark:bg-stone-800/90 dark:text-stone-500 dark:shadow-stone-900/40 dark:hover:bg-stone-700 dark:hover:text-stone-200";

  return (
    <div className="fixed bottom-5 right-5 z-40 flex flex-col gap-2 sm:bottom-6 sm:right-6">
      <button
        type="button"
        onClick={onScrollToTop}
        className={`${buttonClass} ${
          showTop
            ? "pointer-events-auto opacity-100"
            : "pointer-events-none opacity-0"
        }`}
        aria-label={t("common.scrollToTop")}
      >
        <ArrowUp size={16} />
      </button>
      <button
        type="button"
        onClick={onScrollToBottom}
        className={`${buttonClass} ${
          showBottom
            ? "pointer-events-auto opacity-100"
            : "pointer-events-none opacity-0"
        }`}
        aria-label={t("common.scrollToBottom")}
      >
        <ArrowDown size={16} />
      </button>
    </div>
  );
}
