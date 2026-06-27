import { Bot, Boxes, FileText, Sparkles, Wrench, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import type {
  ComposerSelection,
  ComposerSelectionKind,
} from "./composerSelections";

const chipIcons: Record<ComposerSelectionKind, typeof Sparkles> = {
  skill: Sparkles,
  mcp: Wrench,
  agent: Bot,
  model: Boxes,
  file: FileText,
  context: Boxes,
};

export interface ComposerChipsProps {
  selections?: ComposerSelection[];
  chips?: ComposerSelection[];
  onRemove: (id: string) => void;
}

export function ComposerChips({
  selections,
  chips,
  onRemove,
}: ComposerChipsProps) {
  const { t } = useTranslation();
  const visibleSelections = selections ?? chips ?? [];
  if (visibleSelections.length === 0) return null;

  return (
    <div
      className="mx-3 mt-2 flex flex-wrap items-center gap-1.5"
      aria-label={t("chat.selectedComposerContext", "Selected composer context")}
    >
      {visibleSelections.map((selection) => {
        const Icon = chipIcons[selection.kind];
        const unavailable =
          selection.state === "unavailable" ||
          selection.state === "admin-only" ||
          selection.state === "denied";
        return (
          <span
            key={selection.id}
            className={`inline-flex max-w-[220px] items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs ${
              unavailable
                ? "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200"
                : "border-stone-200 bg-stone-50 text-stone-700 dark:border-stone-700 dark:bg-stone-800 dark:text-stone-200"
            }`}
            data-composer-chip-kind={selection.kind}
            data-composer-chip-reference={selection.referenceId ?? selection.id}
            data-composer-chip-state={selection.state}
            data-state={selection.state}
            title={selection.description ?? `${selection.kind}: ${selection.label}`}
          >
            <Icon size={13} className="shrink-0" />
            <span className="truncate font-medium">{selection.label}</span>
            {selection.state !== "enabled" && (
              <span className="shrink-0 text-[10px] opacity-80">
                {t(`composerChip.status.${selection.state}`, selection.state)}
              </span>
            )}
            <button
              type="button"
              onClick={() => onRemove(selection.id)}
              className="ml-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded hover:bg-black/10 dark:hover:bg-white/10"
              aria-label={t("common.remove")}
            >
              <X size={11} />
            </button>
          </span>
        );
      })}
    </div>
  );
}
