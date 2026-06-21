import { Bot, Boxes, FileText, Layers, Sparkles, Wrench } from "lucide-react";
import { useTranslation } from "react-i18next";
import type {
  ComposerCommandName,
  SlashCommandMenuItem,
} from "./chatInputCommands";

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

  return (
    <div
      className="absolute bottom-full left-2 right-2 z-40 mb-2 overflow-hidden rounded-lg border border-stone-200 bg-white shadow-[0_12px_32px_rgba(15,23,42,0.16)] dark:border-stone-800 dark:bg-stone-900"
      role="listbox"
      aria-label={t("composerCommand.menuLabel", "Composer command menu")}
    >
      <div className="border-b border-stone-100 px-3 py-2 text-[11px] font-semibold uppercase text-stone-400 dark:border-stone-800 dark:text-stone-500">
        {t("composerCommand.menuLabel", "Composer command menu")}
      </div>
      <div className="max-h-72 overflow-y-auto p-1.5">
        {items.map((item, index) => {
          const Icon = commandIcons[item.command];
          const active = index === highlightedIndex;
          return (
            <button
              key={item.command}
              type="button"
              role="option"
              aria-selected={active}
              onMouseEnter={() => onHighlight(index)}
              onClick={() => onSelect(item)}
              className="flex min-h-12 w-full items-center gap-3 rounded-md px-2.5 py-2 text-left text-sm transition-colors hover:bg-stone-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-stone-400 dark:hover:bg-stone-800"
              data-active={active ? "" : undefined}
            >
              <span
                className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-md ${
                  active
                    ? "bg-stone-900 text-white dark:bg-stone-100 dark:text-stone-900"
                    : "bg-stone-100 text-stone-500 dark:bg-stone-800 dark:text-stone-300"
                }`}
              >
                <Icon size={16} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-2">
                  <span className="font-medium text-stone-900 dark:text-stone-100">
                    {t(item.labelKey)}
                  </span>
                  <span className="rounded bg-stone-100 px-1.5 py-0.5 font-mono text-[11px] text-stone-500 dark:bg-stone-800 dark:text-stone-300">
                    /{item.command}
                  </span>
                  {item.unavailable && (
                    <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[11px] font-medium text-amber-700 dark:bg-amber-500/10 dark:text-amber-200">
                      {t("composerChip.status.unavailable", "unavailable")}
                    </span>
                  )}
                </span>
                <span className="mt-0.5 block truncate text-xs text-stone-500 dark:text-stone-400">
                  {t(item.descriptionKey)}
                </span>
              </span>
            </button>
          );
        })}
      </div>
      <div className="border-t border-stone-100 px-3 py-2 text-xs text-stone-400 dark:border-stone-800">
        <button
          type="button"
          onClick={onClose}
          className="rounded px-1.5 py-1 hover:bg-stone-100 dark:hover:bg-stone-800"
        >
          Esc
        </button>
      </div>
    </div>
  );
}
