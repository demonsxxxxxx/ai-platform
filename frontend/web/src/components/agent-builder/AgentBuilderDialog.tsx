import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

interface AgentBuilderDialogProps {
  isOpen: boolean;
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}

function focusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(
    container.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  );
}

/** Local dialog primitive with an explicit focus trap and focus restoration. */
export function AgentBuilderDialog({
  isOpen,
  title,
  onClose,
  children,
}: AgentBuilderDialogProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!isOpen) return;

    let focusCancelled = false;
    restoreFocusRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    document.body.style.overflow = "hidden";
    queueMicrotask(() => {
      if (!focusCancelled) closeRef.current?.focus();
    });

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;

      const focusable = dialogRef.current
        ? focusableElements(dialogRef.current)
        : [];
      if (!focusable.length) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable.at(-1)!;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown);
    return () => {
      focusCancelled = true;
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = "";
      restoreFocusRef.current?.focus();
    };
  }, [isOpen]);

  if (!isOpen) return null;

  return createPortal(
    <div className="fixed inset-0 z-[300] flex items-center justify-center p-4">
      <button
        aria-label={`Close ${title}`}
        className="absolute inset-0 cursor-default bg-[var(--theme-overlay-strong)]"
        onClick={() => onCloseRef.current()}
        type="button"
      />
      <div
        ref={dialogRef}
        aria-modal="true"
        aria-label={title}
        role="dialog"
        className="relative z-10 flex max-h-[min(44rem,calc(100vh-2rem))] w-full max-w-2xl flex-col overflow-hidden rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] shadow-[0_18px_48px_rgba(15,23,42,0.24)]"
      >
        <div className="flex items-center justify-between border-b border-[var(--theme-border)] px-5 py-3">
          <h2 className="text-base font-semibold text-[var(--theme-text)]">{title}</h2>
          <button
            ref={closeRef}
            aria-label={`Close ${title}`}
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-[var(--theme-text-secondary)] hover:bg-[var(--theme-workbench-canvas)] hover:text-[var(--theme-text)]"
            onClick={() => onCloseRef.current()}
            type="button"
          >
            <X size={17} aria-hidden="true" />
          </button>
        </div>
        <div className="min-h-0 overflow-y-auto p-5">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
