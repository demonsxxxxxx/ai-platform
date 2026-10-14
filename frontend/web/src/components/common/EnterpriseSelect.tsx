import type { ComponentProps } from "react";
import * as Select from "@radix-ui/react-select";
import { Check, ChevronDown, ChevronUp } from "lucide-react";
import { clsx } from "clsx";

export interface EnterpriseSelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

type EnterpriseSelectProps = Omit<
  ComponentProps<typeof Select.Trigger>,
  "value" | "onChange" | "children" | "defaultValue"
> & {
  value: string;
  onChange: (value: string) => void;
  options: EnterpriseSelectOption[];
  placeholder?: string;
};

/** shadcn/ui Select composition, styled with the workbench's existing theme. */
export function EnterpriseSelect({
  value,
  onChange,
  options,
  disabled = false,
  placeholder,
  className,
  name,
  ...triggerProps
}: EnterpriseSelectProps) {
  const selected = options.find((option) => option.value === value);

  return (
    <Select.Root value={value} onValueChange={onChange} disabled={disabled} name={name}>
      <Select.Trigger
        {...triggerProps}
        className={clsx("enterprise-field-control es-select-btn", className)}
        data-slot="select-trigger"
      >
        <Select.Value placeholder={placeholder} data-slot="select-value">
          {selected?.label ?? placeholder ?? value}
        </Select.Value>
        <Select.Icon asChild>
          <ChevronDown aria-hidden="true" size={15} className="shrink-0 text-[var(--theme-text-secondary)]" />
        </Select.Icon>
      </Select.Trigger>
      <Select.Portal>
        <Select.Content
          className="enterprise-select-dropdown z-[9999] min-w-[var(--radix-select-trigger-width)] max-h-[var(--radix-select-content-available-height)]"
          data-slot="select-content"
          position="popper"
          sideOffset={4}
          collisionPadding={16}
          onEscapeKeyDown={(event) => event.stopPropagation()}
          onKeyDown={(event) => {
            if (event.key === "Tab") event.stopPropagation();
          }}
        >
          <Select.ScrollUpButton className="flex justify-center py-1">
            <ChevronUp aria-hidden="true" size={15} />
          </Select.ScrollUpButton>
          <Select.Viewport>
            {options.map((option) => (
              <Select.Item
                key={option.value}
                value={option.value}
                disabled={option.disabled}
                className="enterprise-select-option"
                data-slot="select-item"
                data-value={option.value}
              >
                <Select.ItemIndicator>
                  <Check aria-hidden="true" size={14} className="enterprise-select-option-check" />
                </Select.ItemIndicator>
                <Select.ItemText>{option.label}</Select.ItemText>
              </Select.Item>
            ))}
          </Select.Viewport>
          <Select.ScrollDownButton className="flex justify-center py-1">
            <ChevronDown aria-hidden="true" size={15} />
          </Select.ScrollDownButton>
        </Select.Content>
      </Select.Portal>
    </Select.Root>
  );
}
