import { Boxes, FileText, Sparkles, Wrench } from "lucide-react";
import { useTranslation } from "react-i18next";
import type {
  ComposerCommandName,
  SlashCommandMenuItem,
} from "./chatInputCommands";
import { ComposerMenu, ComposerMenuItem } from "./assistant-ui/elements/composer";

const commandIcons: Record<
  Exclude<ComposerCommandName, "menu">,
  typeof Sparkles
> = {
  skill: Sparkles,
  mcp: Wrench,
  model: Boxes,
  file: FileText,
};

const commandAlias: Partial<Record<Exclude<ComposerCommandName, "menu">, string>> =
  {
    skill: "$",
  };

export interface SlashCommandMenuProps {
  items: SlashCommandMenuItem[];
  highlightedIndex: number;
  onHighlight: (index: number) => void;
  onSelect: (item: SlashCommandMenuItem) => void;
  onClose: () => void;
}

/** Composer-anchored command menu for the PRD `/` workflow. */
export function SlashCommandMenu({
  items,
  highlightedIndex,
  onHighlight,
  onSelect,
  onClose,
}: SlashCommandMenuProps) {
  const { t } = useTranslation();
  if (items.length === 0) return null;
  const menuLabel = t("composerCommand.menuLabel", "Composer command menu");

  return (
    <ComposerMenu label={menuLabel} onClose={onClose}>
      {items.map((item, index) => {
        const Icon = commandIcons[item.command];
        const active = index === highlightedIndex;
        const alias = commandAlias[item.command];
        return (
          <ComposerMenuItem key={item.command} role="option" aria-selected={active}
            data-composer-command-item={item.command} active={active}
            onMouseEnter={() => onHighlight(index)} onClick={() => onSelect(item)}
            icon={<Icon size={16} />}>
            <span className="min-w-0 flex-1">
              <span className="flex items-center gap-2">
                <span className="font-medium">{t(item.labelKey)}</span>
                <span className="font-mono text-xs">/{item.command}{alias ? ` · ${alias}` : ""}</span>
                {item.unavailable && <span className="text-xs text-[var(--theme-text-secondary)]">{t("composerChip.status.unavailable")}</span>}
              </span>
              <span className="block truncate text-xs text-[var(--theme-text-secondary)]">{t(item.descriptionKey)}</span>
            </span>
            {active && <kbd aria-hidden className="text-xs">↵</kbd>}
          </ComposerMenuItem>
        );
      })}
    </ComposerMenu>
  );
}
