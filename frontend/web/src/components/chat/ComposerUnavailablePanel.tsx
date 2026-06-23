import { Layers, ShieldAlert, X } from "lucide-react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";

export interface ComposerUnavailablePanelProps {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  surface: string;
  title: string;
  description: string;
}

export function ComposerUnavailablePanel({
  isOpen,
  onOpenChange,
  surface,
  title,
  description,
}: ComposerUnavailablePanelProps) {
  const { t } = useTranslation();

  if (!isOpen) return null;

  return createPortal(
    <>
      <div
        data-yields-sidebar
        className="fixed inset-0 z-[300] bg-slate-950/35 animate-fade-in"
        onClick={() => onOpenChange(false)}
      />
      <div
        className="fixed z-[301] sm:inset-0 sm:flex sm:items-center sm:justify-center sm:p-4 inset-x-0 bottom-0 animate-slide-up sm:animate-scale-in"
        onClick={() => onOpenChange(false)}
      >
        <section
          data-composer-unavailable-panel
          data-fail-closed-surface={surface}
          className="flex max-h-[85dvh] w-full flex-col overflow-hidden rounded-t-lg border border-dashed border-[var(--theme-border)] bg-[var(--theme-bg-card)] shadow-[0_8px_24px_rgba(18,38,63,0.12)] dark:border-stone-700 dark:bg-stone-900 sm:w-[40%] sm:min-w-[560px] sm:rounded-lg"
          onClick={(event) => event.stopPropagation()}
        >
          <header className="relative flex items-center justify-between border-b border-slate-200 px-4 py-3 dark:border-stone-800 sm:px-5">
            <div className="absolute left-1/2 top-2 h-1 w-10 -translate-x-1/2 rounded-full bg-slate-300 dark:bg-stone-700 sm:hidden" />
            <div className="mt-2 flex items-center gap-3 sm:mt-0">
              <div className="flex size-9 items-center justify-center rounded-lg bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-200">
                <ShieldAlert size={17} />
              </div>
              <div>
                <h2 className="text-sm font-semibold text-slate-900 dark:text-stone-100">
                  {title}
                </h2>
                <p className="text-xs text-slate-500 dark:text-stone-400">
                  {t(
                    "composerCommand.unavailable.subtitle",
                    "Visible in the composer, but not enabled for your workspace yet.",
                  )}
                </p>
              </div>
            </div>
            <button
              type="button"
              className="rounded-md p-2 text-slate-500 transition-colors hover:bg-slate-100 dark:text-stone-400 dark:hover:bg-stone-800"
              onClick={() => onOpenChange(false)}
              aria-label={t("common.close", "Close")}
            >
              <X size={18} />
            </button>
          </header>

          <div className="p-5">
            <div className="rounded-lg border border-dashed border-amber-200 bg-amber-50 p-4 dark:border-amber-500/30 dark:bg-amber-500/10">
              <div className="flex items-start gap-3">
                <Layers
                  size={18}
                  className="mt-0.5 shrink-0 text-amber-700 dark:text-amber-200"
                />
                <div>
                  <p className="text-sm font-medium text-amber-900 dark:text-amber-100">
                    {t(
                      "composerCommand.unavailable.failClosed",
                      "Fail-closed surface",
                    )}
                  </p>
                  <p className="mt-1 text-sm leading-6 text-amber-800 dark:text-amber-100/80">
                    {description}
                  </p>
                  <p className="mt-3 text-xs font-medium text-amber-700 dark:text-amber-200">
                    {surface === "context-selector"
                      ? "context-selector"
                      : surface}
                  </p>
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>
    </>,
    document.body,
  );
}
