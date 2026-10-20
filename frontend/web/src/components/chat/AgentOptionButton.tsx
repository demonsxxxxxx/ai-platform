import { Button, Drawer, Modal, Popover, Slider } from "@lobehub/ui/base-ui";
import { Field } from "@base-ui/react/field";
import { useState, useEffect, memo } from "react";
import { Brain, Settings, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { AgentOption } from "../../types";
import { ICON_MAP, THINKING_LEVEL_COLOR } from "./chatInputConstants";

interface AgentOptionButtonProps {
  optionKey: string;
  option: AgentOption;
  value: boolean | string | number;
  onChange: (value: boolean | string | number) => void;
  isOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
}

export const AgentOptionButton = memo(function AgentOptionButton({
  optionKey: _optionKey,
  option,
  value,
  onChange,
  isOpen: externalIsOpen,
  onOpenChange: externalOnOpenChange,
}: AgentOptionButtonProps) {
  const { t } = useTranslation();
  const [internalShow, setInternalShow] = useState(false);
  const [isMobile, setIsMobile] = useState(() => typeof window !== "undefined" && window.innerWidth < 640);
  const showDropdown = externalOnOpenChange ? externalIsOpen ?? false : internalShow;
  const setShowDropdown = externalOnOpenChange ?? setInternalShow;
  useEffect(() => {
    const resize = () => setIsMobile(window.innerWidth < 640);
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);
  const label = option.label_key ? t(option.label_key) : option.label;
  const description = option.description_key ? t(option.description_key) : option.description || label;
  const Icon = (option.icon ? ICON_MAP[option.icon] : null) || Settings;
  if (option.type === "boolean") return externalOnOpenChange ? null : (
    <Button type={value === true ? "primary" : "text"} size="large" aria-pressed={value === true}
      aria-label={description} title={description} onClick={() => onChange(!value)} icon={<Icon size={18} />} />
  );
  const options = option.options;
  if (!options?.length) return externalOnOpenChange ? null : (
    <Button type={value !== option.default ? "primary" : "text"} size="large" aria-label={description} title={description}
      onClick={() => onChange(value === option.default ? !option.default : option.default)} icon={<Icon size={18} />} />
  );
  const optionLabel = (item: (typeof options)[number]) => item.label_key ? t(item.label_key) : item.label || String(item.value);
  const selectedLabel = options.find((item) => item.value === value);
  const currentLabel = selectedLabel ? optionLabel(selectedLabel) : String(value);
  const choiceList = <div className="flex flex-col gap-1">{options.map((item) => (
    <Button key={`${typeof item.value}:${item.value}`} type={item.value === value ? "primary" : "text"} block aria-pressed={item.value === value}
      onClick={() => { onChange(item.value); setShowDropdown(false); }}>{optionLabel(item)}</Button>
  ))}</div>;
  if (externalOnOpenChange) return (
    <Modal open={showDropdown} title={description} onCancel={() => setShowDropdown(false)} width={448} draggable={false} closable={false}
      footer={<Button onClick={() => setShowDropdown(false)}>{t("common.close")}</Button>}>{choiceList}</Modal>
  );
  const color = THINKING_LEVEL_COLOR[String(value)] ?? THINKING_LEVEL_COLOR.auto;
  const ActiveIcon = option.icon ? ICON_MAP[option.icon] || Brain : Brain;
  const trigger = <Button type={String(value) === "auto" ? "text" : "default"} size="large"
    aria-label={`${description}: ${currentLabel}`} title={`${description}: ${currentLabel}`}
    style={String(value) === "auto" ? undefined : { color: color.text }}
    onClick={isMobile ? () => setShowDropdown(!showDropdown) : undefined} icon={<ActiveIcon size={18} />} />;
  if (isMobile) return (
    <>
      {trigger}
      <Drawer open={showDropdown} onClose={() => setShowDropdown(false)} placement="bottom" height="auto" title={description} closable={false}
        extra={<Button type="text" aria-label={t("common.close")} onClick={() => setShowDropdown(false)} icon={<X size={18} />} />}
      >{choiceList}</Drawer>
    </>
  );
  return (
    <Popover open={showDropdown} onOpenChange={setShowDropdown} trigger="click" placement="topLeft" content={
      <Field.Root className="w-64">
        <Field.Label className="mb-2 block text-xs text-[var(--theme-text-secondary)]">{description}: {currentLabel}</Field.Label>
        {options.length > 1 ? <>
          <Slider min={0} max={options.length - 1} step={1} largeStep={1}
            value={Math.max(0, options.findIndex((item) => item.value === value))}
            onChange={(index) => onChange(options[index].value)} styles={{ indicator: { background: color.text }, thumb: { background: color.text } }} />
          <div className="grid gap-1" style={{ gridTemplateColumns: `repeat(${options.length}, minmax(0, 1fr))` }}>
            {options.map((item) => <Button key={`${typeof item.value}:${item.value}`} size="small" type="text" className="min-w-0 px-0"
              aria-pressed={item.value === value} title={optionLabel(item)} onClick={() => onChange(item.value)}><span className="truncate">{optionLabel(item)}</span></Button>)}
          </div>
        </> : choiceList}
      </Field.Root>
    }>{trigger}</Popover>
  );
});
