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
      <div
        className="fixed inset-0 z-[299] bg-slate-950/35"
        onClick={onClose}
      />
      <div
        data-yields-sidebar
        className="fixed inset-0 z-[300] flex items-end sm:pointer-events-none sm:items-center sm:justify-center"
      >
        <div
          ref={swipeRef as React.RefObject<HTMLDivElement>}
          className="relative z-10 flex max-h-[90dvh] w-full flex-col overflow-hidden rounded-t-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] shadow-[0_8px_24px_rgba(18,38,63,0.12)] duration-300 animate-slide-up-sheet sm:mx-4 sm:max-w-xl sm:rounded-lg sm:pointer-events-auto sm:animate-in sm:fade-in sm:zoom-in-95 sm:duration-200"
        >
          <div className="flex items-center justify-between border-b border-[var(--theme-border)] px-5 py-4">
            <div className="absolute left-1/2 top-2 h-1 w-9 -translate-x-1/2 rounded-full bg-stone-300 dark:bg-stone-600 sm:hidden" />
            <div className="pt-2 sm:pt-0">
              <h3 className="text-base font-semibold text-[var(--theme-text)]">
                {t("share.title")}
              </h3>
              <p className="mt-1 text-xs leading-5 text-[var(--theme-text-secondary)]">
                {t("share.unavailable.unavailable.description")}
              </p>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg p-1 text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-hover)] hover:text-[var(--theme-text)]"
              aria-label={t("common.close")}
            >
              <X size={20} />
            </button>
          </div>

          <div className="max-h-[70dvh] overflow-y-auto bg-[var(--theme-bg-sidebar)] p-5">
            <ShareUnavailableState reason="unavailable" />
          </div>

          <div className="safe-area-bottom flex items-center justify-end border-t border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-5 py-4">
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-4 py-2 text-sm font-medium text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-hover)] hover:text-[var(--theme-text)]"
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
