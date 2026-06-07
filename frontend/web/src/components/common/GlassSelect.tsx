import { useState, useRef, useEffect, useLayoutEffect } from "react";
import { createPortal } from "react-dom";
import { ChevronDown, Check } from "lucide-react";

export interface GlassSelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

interface GlassSelectProps {
  value: string;
  onChange: (value: string) => void;
  options: GlassSelectOption[];
  disabled?: boolean;
  placeholder?: string;
  className?: string;
}

export function GlassSelect({
  value,
  onChange,
  options,
  disabled = false,
  placeholder,
  className,
}: GlassSelectProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const [dropdownStyle, setDropdownStyle] = useState<React.CSSProperties>({});

  const selected = options.find((o) => o.value === value);
  const displayText = selected
    ? selected.label
    : placeholder ?? options[0]?.label ?? "";

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as Node;
      if (
        ref.current &&
        !ref.current.contains(target) &&
        dropdownRef.current &&
        !dropdownRef.current.contains(target)
      ) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  useLayoutEffect(() => {
    if (!open || !ref.current) return;
    const rect = ref.current.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const w = Math.max(rect.width, 160);
    const left = Math.max(16, Math.min(rect.left, vw - w - 16));

    const spaceBelow = vh - rect.bottom - 16;
    const spaceAbove = rect.top - 16;
    const preferBelow = spaceBelow >= 200 || spaceBelow >= spaceAbove;

    setDropdownStyle({
      position: "fixed",
      top: preferBelow ? rect.bottom + 4 : undefined,
      bottom: preferBelow ? undefined : vh - rect.top + 4,
      left,
      width: w,
      zIndex: 9999,
    });
  }, [open]);

  return (
    <div ref={ref} className={`relative ${className ?? ""}`}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => !disabled && setOpen((v) => !v)}
        className="glass-input es-select-btn"
      >
        <span className="truncate">{displayText}</span>
        <ChevronDown
          size={15}
          className="shrink-0 text-[var(--theme-text-secondary)] transition-transform duration-200"
          style={{ transform: open ? "rotate(180deg)" : undefined }}
        />
      </button>

      {open &&
        createPortal(
          <div
            ref={dropdownRef}
            className="glass-select-dropdown"
            style={dropdownStyle}
          >
            {options.map((opt) => (
              <button
                key={opt.value}
                type="button"
                disabled={opt.disabled}
                onClick={() => {
                  if (opt.disabled) return;
                  onChange(opt.value);
                  setOpen(false);
                }}
                className={`glass-select-option ${
                  opt.value === value ? "active" : ""
                } ${opt.disabled ? "disabled" : ""}`}
              >
                {opt.value === value && (
                  <Check size={14} className="glass-select-option-check" />
                )}
                <span className="glass-select-option-label">{opt.label}</span>
              </button>
            ))}
          </div>,
          document.body,
        )}
    </div>
  );
}
