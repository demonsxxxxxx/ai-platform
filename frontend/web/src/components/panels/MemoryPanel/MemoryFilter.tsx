import { useState, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, Check, Filter } from "lucide-react";
import {
  TYPE_OPTIONS,
  TYPE_DOTS,
  SOURCE_OPTIONS,
  SOURCE_DOTS,
} from "./constants";

export function MemoryFilter({
  typeValue,
  typeOnChange,
  sourceValue,
  sourceOnChange,
}: {
  typeValue: string;
  typeOnChange: (v: string) => void;
  sourceValue: string;
  sourceOnChange: (v: string) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node))
        setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const activeType = TYPE_OPTIONS.find((o) => o.value === typeValue);
  const activeSource = SOURCE_OPTIONS.find((o) => o.value === sourceValue);
  const hasFilter = typeValue || sourceValue;

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="panel-search h-10 !pl-3 !pr-3 inline-flex items-center gap-1.5 font-sans cursor-pointer text-sm text-[var(--theme-text)]"
      >
        <Filter size={14} className="text-[var(--theme-text-secondary)]" />
        <span>
          {hasFilter
            ? [
                activeType && t(activeType.labelKey),
                activeSource && t(activeSource.labelKey),
              ]
                .filter(Boolean)
                .join(" / ")
            : t("memory.allTypes")}
        </span>
        <ChevronDown
          size={14}
          className={`text-[var(--theme-text-secondary)] transition-transform ${
            open ? "rotate-180" : ""
          }`}
        />
      </button>

      {open && (
        <div className="absolute right-0 z-10 mt-1 w-44 rounded-xl border border-[var(--theme-border)] bg-[var(--theme-bg-card)] py-2 shadow-xl dark:shadow-black/40 animate-in fade-in-0 zoom-in-95 duration-100">
          <div className="px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-[var(--theme-text-secondary)]">
            {t("memory.typeLabel")}
          </div>
          {TYPE_OPTIONS.map((opt) => {
            const d = opt.value ? TYPE_DOTS[opt.value] : null;
            return (
              <button
                key={opt.value}
                onClick={() => typeOnChange(opt.value)}
                className={`flex w-full items-center gap-2 px-3 py-2 text-sm transition-colors ${
                  typeValue === opt.value
                    ? "bg-[var(--theme-primary-light)] text-[var(--theme-text)]"
                    : "text-[var(--theme-text-secondary)] hover:bg-[var(--glass-bg)]"
                }`}
              >
                {d && <span className={`h-2 w-2 rounded-full ${d}`} />}
                <span className="flex-1 text-left">{t(opt.labelKey)}</span>
                {typeValue === opt.value && (
                  <Check
                    size={14}
                    className="text-[var(--theme-text-secondary)]"
                  />
                )}
              </button>
            );
          })}
          <div className="my-1 border-t border-[var(--glass-border)]" />
          <div className="px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-[var(--theme-text-secondary)]">
            {t("memory.sourceLabel")}
          </div>
          {SOURCE_OPTIONS.map((opt) => {
            const d = opt.value ? SOURCE_DOTS[opt.value] : null;
            return (
              <button
                key={opt.value}
                onClick={() => sourceOnChange(opt.value)}
                className={`flex w-full items-center gap-2 px-3 py-2 text-sm transition-colors ${
                  sourceValue === opt.value
                    ? "bg-[var(--theme-primary-light)] text-[var(--theme-text)]"
                    : "text-[var(--theme-text-secondary)] hover:bg-[var(--glass-bg)]"
                }`}
              >
                {d && <span className={`h-2 w-2 rounded-full ${d}`} />}
                <span className="flex-1 text-left">{t(opt.labelKey)}</span>
                {sourceValue === opt.value && (
                  <Check
                    size={14}
                    className="text-[var(--theme-text-secondary)]"
                  />
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
