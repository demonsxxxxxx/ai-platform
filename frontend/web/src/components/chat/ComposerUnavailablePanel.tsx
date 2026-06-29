import { Layers, ShieldAlert, X } from "lucide-react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { LibreChatStateSurface } from "../../librechat-ui/StateSurface";

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
        className="fixed inset-0 z-[300] bg-[var(--theme-overlay)] animate-fade-in"
        onClick={() => onOpenChange(false)}
      />
      <div
        className="fixed z-[301] sm:inset-0 sm:flex sm:items-center sm:justify-center sm:p-4 inset-x-0 bottom-0 animate-slide-up sm:animate-scale-in"
        onClick={() => onOpenChange(false)}
      >
        <section
          data-composer-unavailable-panel
          data-fail-closed-surface={surface}
          className="flex max-h-[85dvh] w-full flex-col overflow-hidden rounded-t-lg border border-dashed border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] text-[var(--theme-text)] shadow-[0_8px_24px_rgba(18,38,63,0.12)] sm:w-[40%] sm:min-w-[560px] sm:rounded-lg"
          onClick={(event) => event.stopPropagation()}
        >
          <header className="relative flex items-center justify-between border-b border-[var(--theme-border)] px-4 py-3 sm:px-5">
            <div className="absolute left-1/2 top-2 h-1 w-10 -translate-x-1/2 rounded-full bg-[var(--theme-border)] sm:hidden" />
            <div className="mt-2 flex items-center gap-3 sm:mt-0">
              <div className="flex size-9 items-center justify-center rounded-lg bg-[var(--theme-warning-soft)] text-[var(--theme-warning)] ring-1 ring-[var(--theme-warning-ring)]">
                <ShieldAlert size={17} />
              </div>
              <div>
                <h2 className="text-sm font-semibold text-[var(--theme-text)]">
                  {title}
                </h2>
                <p className="text-xs text-[var(--theme-text-secondary)]">
                  {t(
                    "composerCommand.unavailable.subtitle",
                    "Visible in the composer, but not enabled for your workspace yet.",
                  )}
                </p>
              </div>
            </div>
            <button
              type="button"
              className="rounded-md p-2 text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-bg-sidebar)] hover:text-[var(--theme-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-ring)]"
              onClick={() => onOpenChange(false)}
              aria-label={t("common.close", "Close")}
            >
              <X size={18} />
            </button>
          </header>

          <div className="p-5">
            <LibreChatStateSurface
              state="forbidden"
              surface={surface}
              icon={Layers}
              title={t(
                "composerCommand.unavailable.failClosed",
                "Fail-closed surface",
              )}
              description={description}
              className="border-dashed border-[var(--theme-warning-ring)] bg-[var(--theme-warning-soft)] text-left"
            >
              <p className="mt-3 text-xs font-medium text-[var(--theme-warning)]">
                {surface === "context-selector" ? "context-selector" : surface}
              </p>
            </LibreChatStateSurface>
          </div>
        </section>
      </div>
    </>,
    document.body,
  );
}
