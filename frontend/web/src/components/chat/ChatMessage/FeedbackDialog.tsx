import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { ThumbsUp, ThumbsDown, X, Send } from "lucide-react";
import { clsx } from "clsx";
import { useTranslation } from "react-i18next";
import { useSwipeToClose } from "../../../hooks/useSwipeToClose";
import type { RatingValue } from "../../../types/feedback";

interface FeedbackDialogProps {
  isOpen: boolean;
  onClose: () => void;
  rating: RatingValue;
  comment: string;
  onCommentChange: (value: string) => void;
  onSubmit: () => void;
  onSkip: () => void;
  isSubmitting: boolean;
}

export function FeedbackDialog({
  isOpen,
  onClose,
  rating,
  comment,
  onCommentChange,
  onSubmit,
  onSkip,
  isSubmitting,
}: FeedbackDialogProps) {
  const { t } = useTranslation();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const swipeRef = useSwipeToClose({ onClose, enabled: isOpen });

  useEffect(() => {
    if (isOpen && textareaRef.current) {
      textareaRef.current.focus();
    }
  }, [isOpen]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (!isOpen) return;
      if (e.key === "Escape") {
        onClose();
      } else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        onSubmit();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose, onSubmit]);

  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [isOpen]);

  if (!isOpen) return null;

  return createPortal(
    <>
      <div
        className="fixed inset-0 z-[299] bg-slate-950/35"
        onClick={onClose}
      />

      <div
        data-yields-sidebar
        className="fixed inset-0 z-[300] flex items-end sm:items-center sm:justify-center sm:pointer-events-none"
      >
        <div
          ref={swipeRef as React.RefObject<HTMLDivElement>}
          className="relative z-10 w-full overflow-hidden rounded-t-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] shadow-[0_8px_24px_rgba(18,38,63,0.12)] duration-300 animate-slide-up-sheet sm:mx-4 sm:max-w-md sm:pointer-events-auto sm:rounded-lg sm:animate-in sm:fade-in sm:zoom-in-95 sm:duration-200"
        >
          {/* Header */}
          <div className="flex items-center justify-between border-b border-[var(--theme-border)] px-5 py-4">
            <div className="sm:hidden absolute top-2 left-1/2 -translate-x-1/2 w-9 h-1 bg-stone-300 dark:bg-stone-600 rounded-full" />
            <div className="flex items-center gap-2 pt-2 sm:pt-0">
              <span
                className={clsx(
                  "enterprise-avatar h-7 w-7",
                )}
              >
                {rating === "up" ? (
                  <ThumbsUp size={14} />
                ) : (
                  <ThumbsDown size={14} />
                )}
              </span>
              <h3 className="text-lg font-semibold text-[var(--theme-text)]">
                {rating === "up"
                  ? t("feedback.positive")
                  : t("feedback.negative")}
              </h3>
            </div>
            <button
              onClick={onClose}
              className="btn-icon"
            >
              <X size={20} />
            </button>
          </div>

          {/* Content */}
          <div className="p-5">
            <textarea
              ref={textareaRef}
              value={comment}
              onChange={(e) => onCommentChange(e.target.value)}
              placeholder={
                t("feedback.commentPlaceholder") || "What could be improved?"
              }
              className={clsx(
                "enterprise-form-textarea text-sm",
              )}
              rows={4}
            />
            <div className="mt-2 text-right text-xs text-[var(--theme-text-secondary)]">
              {t("feedback.pressEnter") || "⌘+Enter to send"}
            </div>
          </div>

          {/* Footer */}
          <div className="safe-area-bottom flex items-center justify-end gap-2 border-t border-[var(--theme-border)] bg-[var(--theme-bg)] px-5 py-4">
            <button
              onClick={onSkip}
              disabled={isSubmitting}
              className="btn-secondary disabled:cursor-not-allowed disabled:opacity-50"
            >
              {t("common.skip") || "Skip"}
            </button>
            <button
              onClick={onSubmit}
              disabled={isSubmitting}
              className="btn-primary disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isSubmitting ? (
                <span className="relative h-4 w-4">
                  <span className="absolute inset-0 rounded-full border-2 border-white/30 dark:border-stone-700" />
                  <span className="absolute inset-0 rounded-full border-2 border-transparent border-t-white dark:border-t-stone-300 animate-spin will-change-transform" />
                </span>
              ) : (
                <Send size={14} />
              )}
              <span>{t("feedback.submit") || "Submit"}</span>
            </button>
          </div>
        </div>
      </div>
    </>,
    document.body,
  );
}
