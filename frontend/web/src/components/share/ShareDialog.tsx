/**
 * ShareDialog - fail-closed dialog for governed ai-platform session shares.
 */

import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useSwipeToClose } from "../../hooks/useSwipeToClose";
import { ShareUnavailableState } from "./ShareUnavailableState";

interface ShareDialogProps {
  isOpen: boolean;
  onClose: () => void;
  sessionId: string;
  sessionName: string;
  currentRunId?: string;
}

export function ShareDialog({
  isOpen,
  onClose,
  sessionId: _sessionId,
  sessionName: _sessionName,
  currentRunId: _currentRunId,
}: ShareDialogProps) {
  const { t } = useTranslation();
  const swipeRef = useSwipeToClose({
    onClose,
    enabled: isOpen,
  });

  if (!isOpen) return null;

  return createPortal(
    <>
      <div className="fixed inset-0 z-[299] bg-black/50" onClick={onClose} />
      <div
        data-yields-sidebar
        className="fixed inset-0 z-[300] flex items-end sm:pointer-events-none sm:items-center sm:justify-center"
      >
        <div
          ref={swipeRef as React.RefObject<HTMLDivElement>}
          className="relative z-10 flex max-h-[90dvh] w-full flex-col overflow-hidden rounded-t-xl border border-stone-200 bg-white shadow-xl duration-300 animate-slide-up-sheet dark:border-stone-700 dark:bg-stone-800 sm:mx-4 sm:max-w-xl sm:rounded-xl sm:pointer-events-auto sm:animate-in sm:fade-in sm:zoom-in-95 sm:duration-200"
        >
          <div className="flex items-center justify-between border-b border-stone-200 px-5 py-4 dark:border-stone-700">
            <div className="absolute left-1/2 top-2 h-1 w-9 -translate-x-1/2 rounded-full bg-stone-300 dark:bg-stone-600 sm:hidden" />
            <div className="pt-2 sm:pt-0">
              <h3 className="text-lg font-semibold text-stone-900 dark:text-stone-100">
                {t("share.title")}
              </h3>
              <p className="mt-1 text-xs leading-5 text-stone-500 dark:text-stone-400">
                {t("share.unavailable.unavailable.description")}
              </p>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg p-1 transition-colors hover:bg-stone-100 dark:hover:bg-stone-700"
              aria-label={t("common.close")}
            >
              <X size={20} className="text-stone-500 dark:text-stone-400" />
            </button>
          </div>

          <div className="max-h-[70dvh] overflow-y-auto p-5">
            <ShareUnavailableState reason="unavailable" />
          </div>

          <div className="safe-area-bottom flex items-center justify-end border-t border-stone-100 bg-stone-50 px-5 py-4 dark:border-stone-700 dark:bg-stone-900/50">
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg border border-stone-200 bg-white px-4 py-2 text-sm font-medium text-stone-700 transition-colors hover:bg-stone-50 dark:border-stone-600 dark:bg-stone-800 dark:text-stone-300 dark:hover:bg-stone-700"
            >
              {t("common.close")}
            </button>
          </div>
        </div>
      </div>
    </>,
    document.body,
  );
}
