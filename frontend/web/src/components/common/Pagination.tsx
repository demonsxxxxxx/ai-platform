/**
 * Pagination Component - Page number navigation with mobile responsive layout
 */

import { useState, useEffect } from "react";
import { ChevronLeft, ChevronRight, MoreHorizontal } from "lucide-react";
import { useTranslation } from "react-i18next";

interface PaginationProps {
  page: number;
  pageSize: number;
  total: number;
  onChange: (page: number) => void;
}

function useIsMobile(breakpoint = 640) {
  const [isMobile, setIsMobile] = useState(
    typeof window !== "undefined" ? window.innerWidth < breakpoint : false,
  );

  useEffect(() => {
    const mql = window.matchMedia(`(max-width: ${breakpoint}px)`);
    const handler = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    mql.addEventListener("change", handler);
    return () => mql.removeEventListener("change", handler);
  }, [breakpoint]);

  return isMobile;
}

export function Pagination({
  page,
  pageSize,
  total,
  onChange,
}: PaginationProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const totalPages = Math.ceil(total / pageSize);

  if (totalPages <= 1) return null;

  const pages = getPageNumbers(page, totalPages, isMobile);
  const startItem = (page - 1) * pageSize + 1;
  const endItem = Math.min(page * pageSize, total);

  return (
    <div className="pagination-wrapper">
      {/* Info */}
      <p
        className={`text-stone-500 dark:text-stone-400 whitespace-nowrap ${
          isMobile ? "text-xs" : "text-sm"
        }`}
      >
        {isMobile
          ? `${page} / ${totalPages}`
          : `${startItem}-${endItem} / ${total}`}
      </p>

      {/* Page controls */}
      <div className="pagination-controls">
        <button
          onClick={() => onChange(page - 1)}
          disabled={page === 1}
          className="pagination-btn"
          aria-label={t("common.previous")}
        >
          <ChevronLeft size={isMobile ? 14 : 16} />
        </button>

        {pages.map((p, idx) =>
          p === "..." ? (
            <span key={`ellipsis-${idx}`} className="pagination-ellipsis">
              <MoreHorizontal size={isMobile ? 12 : 14} />
            </span>
          ) : (
            <button
              key={p}
              onClick={() => onChange(p as number)}
              className={`pagination-page ${
                p === page ? "pagination-page-active" : ""
              }`}
            >
              {p}
            </button>
          ),
        )}

        <button
          onClick={() => onChange(page + 1)}
          disabled={page === totalPages}
          className="pagination-btn"
          aria-label={t("common.next")}
        >
          <ChevronRight size={isMobile ? 14 : 16} />
        </button>
      </div>
    </div>
  );
}

/**
 * Generate page numbers with ellipsis for large page counts.
 * On mobile (compact=true), shows fewer pages around current.
 */
function getPageNumbers(
  current: number,
  total: number,
  compact = false,
): (number | string)[] {
  const maxVisible = compact ? 3 : 7;

  if (total <= maxVisible) {
    return Array.from({ length: total }, (_, i) => i + 1);
  }

  const pages: (number | string)[] = [];

  if (compact) {
    const start = Math.max(1, current - 1);
    const end = Math.min(total, current + 1);

    if (start > 1) {
      pages.push(1);
      if (start > 2) pages.push("...");
    }

    for (let i = start; i <= end; i++) {
      pages.push(i);
    }

    if (end < total) {
      if (end < total - 1) pages.push("...");
      pages.push(total);
    }
  } else {
    pages.push(1);

    if (current > 3) {
      pages.push("...");
    }

    const start = Math.max(2, current - 1);
    const end = Math.min(total - 1, current + 1);

    for (let i = start; i <= end; i++) {
      pages.push(i);
    }

    if (current < total - 2) {
      pages.push("...");
    }

    if (total > 1) {
      pages.push(total);
    }
  }

  return pages;
}

export default Pagination;
