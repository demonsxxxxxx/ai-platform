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

  return (
    <div
      className="composer-command-surface absolute bottom-full left-2 right-2 z-40 mb-2 border border-[var(--theme-border)] bg-[var(--theme-bg-card)] shadow-[0_12px_28px_rgba(15,23,42,0.12)] dark:bg-stone-900"
      role="listbox"
      data-composer-command-menu
      aria-label={t("composerCommand.menuLabel", "Composer command menu")}
    >
      <div className="border-b border-[var(--theme-border)] px-3 py-2 text-[11px] font-semibold uppercase text-[var(--theme-text-secondary)]">
        {t("composerCommand.menuLabel", "Composer command menu")}
      </div>
      <div className="composer-command-list p-1.5">
        {items.map((item, index) => {
          const Icon = commandIcons[item.command];
          const active = index === highlightedIndex;
          const alias = commandAlias[item.command];
          return (
            <button
              key={item.command}
              type="button"
              role="option"
              aria-selected={active}
              onMouseEnter={() => onHighlight(index)}
              onClick={() => onSelect(item)}
              className="flex min-h-12 w-full items-center gap-3 rounded-md px-2.5 py-2 text-left text-sm transition-colors hover:bg-[var(--theme-bg-sidebar)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-ring)]"
              data-composer-command-item={item.command}
              data-active={active ? "" : undefined}
            >
              <span
                className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-md ${
                  active
                    ? "bg-[var(--theme-primary)] text-white dark:text-stone-950"
                    : "bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)]"
                }`}
              >
                <Icon size={16} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-2">
                  <span className="font-medium text-[var(--theme-text)]">
                    {t(item.labelKey)}
                  </span>
                  <span className="rounded bg-[var(--theme-bg-sidebar)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--theme-text-secondary)]">
                    /{item.command}
                  </span>
                  {alias && (
                    <span className="rounded bg-[var(--theme-bg-sidebar)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--theme-text-secondary)]">
                      {alias}
                    </span>
                  )}
                  {item.unavailable && (
                    <span className="rounded bg-[var(--theme-bg-sidebar)] px-1.5 py-0.5 text-[11px] font-medium text-[var(--theme-text-secondary)]">
                      {t("composerChip.status.unavailable", "unavailable")}
                    </span>
                  )}
                </span>
                <span className="mt-0.5 block truncate text-xs text-[var(--theme-text-secondary)]">
                  {t(item.descriptionKey)}
                </span>
              </span>
            </button>
          );
        })}
      </div>
      <div className="border-t border-[var(--theme-border)] px-3 py-2 text-xs text-[var(--theme-text-secondary)]">
        <button
          type="button"
          onClick={onClose}
          className="rounded px-1.5 py-1 hover:bg-[var(--theme-bg-sidebar)]"
        >
          Esc
        </button>
      </div>
    </div>
  );
}
