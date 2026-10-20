import { useId, useState, type ComponentProps } from "react";
import { Select } from "@lobehub/ui/base-ui";

type LobeSelectFieldProps = Omit<ComponentProps<typeof Select>, "value" | "onChange"> & {
  value: string;
  onChange: (value: string) => void;
  "aria-label": string;
};

/** Select does not forward aria-label; a real label also works across its portal. */
export function LobeSelectField({ "aria-label": label, id, onChange, onOpenChange, open, className, style, ...props }: LobeSelectFieldProps) {
  const generatedId = useId();
  const controlId = id ?? generatedId;
  const [uncontrolledOpen, setUncontrolledOpen] = useState(props.defaultOpen ?? false);
  const isOpen = open ?? uncontrolledOpen;
  return (
    <span className={`inline-flex min-w-0 shrink-0 ${className ?? ""}`} style={style} onKeyDown={(event) => {
      if (isOpen && (event.key === "Escape" || event.key === "Tab")) event.stopPropagation();
    }}>
      <label className="sr-only" htmlFor={controlId}>{label}</label>
      <Select {...props} id={controlId} open={open} onOpenChange={(next, details) => {
        setUncontrolledOpen(next);
        onOpenChange?.(next, details);
      }} onChange={(value) => {
        if (typeof value === "string") onChange(value);
      }} />
    </span>
  );
}
