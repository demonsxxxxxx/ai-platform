import { FileText, Layers, Sparkles, Wrench } from "lucide-react";
import { useTranslation } from "react-i18next";

export interface ComposerCommandHintBarProps {
  onCommand: (command: "$" | "/skill" | "/mcp" | "/file" | "/context") => void;
  skillsAvailable: boolean;
  mcpAvailable: boolean;
  filesAvailable: boolean;
  contextAvailable: boolean;
}

const shortcutItems = [
  {
    id: "skills",
    command: "$" as const,
    icon: Sparkles,
    labelKey: "composerCommand.shortcut.skills",
    fallback: "$ Skills",
  },
  {
    id: "mcp",
    command: "/mcp" as const,
    icon: Wrench,
    labelKey: "composerCommand.shortcut.mcp",
    fallback: "/mcp",
  },
  {
    id: "file",
    command: "/file" as const,
    icon: FileText,
    labelKey: "composerCommand.shortcut.file",
    fallback: "/file",
  },
  {
    id: "context",
    command: "/context" as const,
    icon: Layers,
    labelKey: "composerCommand.shortcut.context",
    fallback: "/context",
  },
];

export function ComposerCommandHintBar({
  onCommand,
  skillsAvailable,
  mcpAvailable,
  filesAvailable,
  contextAvailable,
}: ComposerCommandHintBarProps) {
  const { t } = useTranslation();
  const availability: Record<(typeof shortcutItems)[number]["id"], boolean> = {
    skills: skillsAvailable,
    mcp: mcpAvailable,
    file: filesAvailable,
    context: contextAvailable,
  };

  return (
    <div
      className="mx-3 mt-2 flex flex-wrap items-center gap-1.5"
      data-composer-command-hints
      aria-label={t(
        "composerCommand.shortcut.label",
        "Composer shortcuts",
      )}
    >
      {shortcutItems.map((item) => {
        const Icon = item.icon;
        const available = availability[item.id];
        return (
          <button
            key={item.id}
            type="button"
            onClick={() => onCommand(item.command)}
            disabled={!available}
            className={`inline-flex min-h-8 items-center gap-1.5 rounded-lg border px-2.5 text-xs font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-stone-400 ${
              available
                ? "border-[var(--theme-border)] bg-[var(--theme-bg-card)] text-[var(--theme-text)] hover:bg-[var(--theme-bg-sidebar)]"
                : "border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)] opacity-80"
            }`}
            aria-disabled={!available}
            data-governed-unavailable={!available ? "" : undefined}
            title={
              available
                ? t(item.labelKey, item.fallback)
                : t(
                    "composerCommand.shortcut.unavailable",
                    "Visible, but not enabled for your workspace yet",
                  )
            }
          >
            <Icon size={13} className="shrink-0" />
            <span>{t(item.labelKey, item.fallback)}</span>
            {!available && (
              <span className="text-[10px] opacity-80">
                {t("composerChip.status.unavailable", "unavailable")}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
