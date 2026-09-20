import { X } from "lucide-react";
import { useEffect, useRef } from "react";

function statusLabel(status: string): string {
  return (
    {
      draft: "待检查",
      checking: "检查中",
      cataloging: "同步中",
      active: "可用",
      unavailable: "不可用",
      disabled: "已停用",
      pending_review: "待审核",
      missing: "上游已缺失",
    }[status] ?? status
  );
}

export function StatusBadge({ status }: { status: string }) {
  const healthy = status === "active";
  const warning = status === "draft" || status === "pending_review";
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-1 text-xs font-medium ring-1 ${
        healthy
          ? "bg-emerald-500/10 text-emerald-700 ring-emerald-500/20 dark:text-emerald-300"
          : warning
            ? "bg-amber-500/10 text-amber-700 ring-amber-500/20 dark:text-amber-300"
            : "bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)] ring-[var(--theme-border)]"
      }`}
    >
      {statusLabel(status)}
    </span>
  );
}

export function CatalogPager({
  page,
  hasNext,
  onPrevious,
  onNext,
}: {
  page: number;
  hasNext: boolean;
  onPrevious: () => void;
  onNext: () => void;
}) {
  return (
    <nav
      aria-label="目录分页"
      className="flex items-center justify-end gap-2 border-t border-[var(--theme-border)] px-4 py-3"
    >
      <span className="mr-2 text-xs text-[var(--theme-text-secondary)]">
        第 {page + 1} 页
      </span>
      <button
        type="button"
        className="btn-secondary"
        disabled={page === 0}
        onClick={onPrevious}
      >
        上一页
      </button>
      <button
        type="button"
        className="btn-secondary"
        disabled={!hasNext}
        onClick={onNext}
      >
        下一页
      </button>
    </nav>
  );
}

export function ModalShell({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const panelRef = useRef<HTMLElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const panel = panelRef.current;
    const focusableSelector = [
      "a[href]",
      "button:not([disabled])",
      "input:not([disabled])",
      "select:not([disabled])",
      "textarea:not([disabled])",
      '[tabindex]:not([tabindex="-1"])',
    ].join(",");
    const focusable = () =>
      Array.from(
        panel?.querySelectorAll<HTMLElement>(focusableSelector) ?? [],
      ).filter((element) => element.getAttribute("aria-hidden") !== "true");
    const initial =
      panel?.querySelector<HTMLElement>("[autofocus]") ?? focusable()[0] ?? panel;
    initial?.focus();

    const containFocus = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const candidates = focusable();
      if (candidates.length === 0) {
        event.preventDefault();
        panel?.focus();
        return;
      }
      const first = candidates[0];
      const last = candidates[candidates.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", containFocus);
    return () => {
      document.removeEventListener("keydown", containFocus);
      previous?.focus();
    };
  }, []);

  return (
    <div
      className="fixed inset-0 z-[300] flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <button
        className="absolute inset-0 bg-[var(--theme-overlay-strong)]"
        onClick={onClose}
        aria-label="关闭"
        tabIndex={-1}
        type="button"
      />
      <section
        ref={panelRef}
        tabIndex={-1}
        className="enterprise-modal-shell relative z-10 max-h-[calc(100dvh-2rem)] w-full max-w-xl overflow-y-auto"
      >
        <header className="enterprise-modal-footer justify-between">
          <h2 className="text-base font-semibold text-[var(--theme-text)]">
            {title}
          </h2>
          <button
            className="btn-icon"
            onClick={onClose}
            type="button"
            aria-label="关闭"
          >
            <X size={18} />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}
