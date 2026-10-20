// Adapted from assistant-ui Elements option-list.tsx at 727a02dc (MIT).
import { Button } from "@lobehub/ui/base-ui";
import { Check } from "lucide-react";
import { useLayoutEffect, useRef, useState, type ComponentProps } from "react";
import { useTranslation } from "react-i18next";

export interface OptionListOption { id: string; label: string; description?: string; disabled?: boolean }
export interface OptionListProps extends Omit<ComponentProps<"div">, "children" | "defaultValue" | "onChange"> {
  options: readonly OptionListOption[];
  selectionMode?: "single" | "multiple";
  defaultValue?: readonly string[];
  value?: readonly string[];
  onValueChange?: (value: string[]) => void;
  onConfirm?: (value: string[]) => void | Promise<void>;
  choice?: readonly string[];
  minSelections?: number;
  maxSelections?: number;
  confirmLabel?: string;
  disabled?: boolean;
}

function OptionText({ option }: { option: OptionListOption }) {
  return <span className="flex min-w-0 flex-1 flex-col gap-0.5 text-left">
    <span className="break-words text-sm leading-5">{option.label}</span>
    {option.description && <span className="break-words text-xs leading-4 text-[var(--theme-text-secondary)]">{option.description}</span>}
  </span>;
}

export function OptionList({ options, selectionMode = "single", defaultValue, value, onValueChange, onConfirm,
  choice, minSelections = 1, maxSelections, confirmLabel, disabled = false, className, ...props }: OptionListProps) {
  const { t } = useTranslation();
  const multiple = selectionMode === "multiple";
  const [selected, setSelected] = useState<readonly string[]>(defaultValue ?? []);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const focusedRef = useRef(false);
  useLayoutEffect(() => {
    if (choice !== undefined && focusedRef.current) rootRef.current?.focus();
  }, [choice]);
  const max = Math.floor(Math.max(0, Math.min(maxSelections ?? options.length, options.length)));
  const min = Math.floor(Math.max(0, Math.min(minSelections, max)));
  const selectedIds = (value ?? selected).filter((id) => options.some((option) => option.id === id));
  const locked = disabled || pending;
  const change = (ids: string[]) => {
    if (value === undefined) setSelected(ids);
    onValueChange?.(ids);
  };
  const commit = async (ids: string[]) => {
    if (!onConfirm || locked) return;
    setPending(true);
    setError(false);
    try { await onConfirm(ids); }
    catch { setError(true); }
    finally { setPending(false); }
    // Only the platform's choice projection can turn this view into a receipt.
  };
  return <div {...props} ref={rootRef} tabIndex={choice !== undefined ? -1 : undefined}
    data-slot="option-list" data-state={choice !== undefined ? "receipt" : pending ? "pending" : "open"}
    aria-busy={pending || undefined} className={className}
    onFocusCapture={() => { focusedRef.current = true; }}
    onBlurCapture={(event) => { if (event.relatedTarget && !event.currentTarget.contains(event.relatedTarget)) focusedRef.current = false; }}>
    {choice !== undefined ? (
      <div className="flex flex-col gap-1">
        {options.filter((option) => choice.includes(option.id)).map((option) => <div key={option.id}
          className="flex items-start gap-2.5 rounded-xl px-2 py-2 text-[var(--theme-text-secondary)]">
          <Check aria-hidden className="mt-0.5 size-4 shrink-0" /><OptionText option={option} />
        </div>)}
        {!options.some((option) => choice.includes(option.id)) && <span className="text-xs">{t("assistantUi.options.noSelection")}</span>}
      </div>
    ) : <>
      <div className="flex flex-col gap-1" role="group">
        {options.map((option) => {
          const checked = selectedIds.includes(option.id);
          const unavailable = locked || option.disabled || (multiple && !checked && selectedIds.length >= max);
          return <Button key={option.id} type="text" htmlType="button" block
            role={multiple ? "checkbox" : undefined} aria-checked={multiple ? checked : undefined} aria-pressed={!multiple ? checked : undefined} aria-label={option.label}
            disabled={unavailable} onClick={() => {
              const ids = multiple ? checked ? selectedIds.filter((id) => id !== option.id) : [...selectedIds, option.id] : [option.id];
              change(ids);
              if (!multiple) void commit(ids);
            }} style={{ height: "auto", minHeight: 44, padding: "8px 10px", justifyContent: "flex-start", whiteSpace: "normal", background: checked ? "var(--theme-bg-sidebar)" : undefined }}>
            <span aria-hidden className="flex size-4 shrink-0 items-center justify-center">{checked && <Check size={14} />}</span>
            <OptionText option={option} />
          </Button>;
        })}
      </div>
      {multiple && onConfirm && <div className="mt-2 flex items-center justify-between gap-3 px-2">
        <span className="text-xs tabular-nums text-[var(--theme-text-secondary)]">{selectedIds.length} / {max}</span>
        <Button type="primary" htmlType="button" size="small" loading={pending} disabled={locked || selectedIds.length < min || selectedIds.length > max}
          onClick={() => void commit(options.filter((option) => selectedIds.includes(option.id)).map((option) => option.id))}>{confirmLabel ?? t("common.confirm")}</Button>
      </div>}
      {error && <p role="alert" className="mt-2 text-xs text-[var(--theme-danger)]">{t("assistantUi.options.unconfirmed")}</p>}
    </>}
  </div>;
}
