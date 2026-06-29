import { Bot, Boxes, FileText, Layers, Sparkles, Wrench } from "lucide-react";
import { useTranslation } from "react-i18next";
import type {
  ComposerCommandName,
  SlashCommandMenuItem,
} from "./chatInputCommands";
import {
  LibreChatCommandMenu,
  LibreChatCommandMenuItem,
} from "../../librechat-ui/CommandMenu";

const commandIcons: Record<
  Exclude<ComposerCommandName, "menu">,
  typeof Sparkles
> = {
  skill: Sparkles,
  mcp: Wrench,
  agent: Bot,
  model: Boxes,
  file: FileText,
  context: Layers,
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
    <LibreChatCommandMenu label={menuLabel} closeLabel="Esc" onClose={onClose}>
      {items.map((item, index) => {
        const Icon = commandIcons[item.command];
        const active = index === highlightedIndex;
        const alias = commandAlias[item.command];
        return (
          <LibreChatCommandMenuItem
            key={item.command}
            id={item.command}
            label={t(item.labelKey)}
            description={t(item.descriptionKey)}
            command={item.command}
            alias={alias}
            unavailable={item.unavailable}
            unavailableLabel={t("composerChip.status.unavailable", "unavailable")}
            active={active}
            icon={Icon}
            onHighlight={() => onHighlight(index)}
            onSelect={() => onSelect(item)}
          />
        );
      })}
    </LibreChatCommandMenu>
  );
}
