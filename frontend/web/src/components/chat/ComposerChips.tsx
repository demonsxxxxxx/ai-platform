import { Boxes, FileText, Sparkles, Wrench } from "lucide-react";
import { useTranslation } from "react-i18next";
import type {
  ComposerSelection,
  ComposerSelectionKind,
} from "./composerSelections";
import { ComposerChip } from "./assistant-ui/elements/composer-chips";

const chipIcons: Record<ComposerSelectionKind, typeof Sparkles> = {
  skill: Sparkles,
  mcp: Wrench,
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
        const statusLabel = t(
          `composerChip.status.${selection.state}`,
          selection.state,
        );
        const chip = <ComposerChip
          id={selection.id} kind={selection.kind} label={selection.label} state={selection.state}
          description={selection.description} referenceId={selection.referenceId} icon={Icon}
          statusLabel={statusLabel} onRemove={onRemove}
          removeLabel={selection.kind === "skill" ? t("skillSelector.removeSelectedSkill", { name: selection.label }) : t("common.remove")} />;
        return selection.kind === "skill" && selection.visibleDetails?.length ? (
          <span
            key={selection.id}
            className="inline-flex max-w-full flex-wrap items-center gap-x-2 gap-y-1"
          >
            {chip}
            {selection.visibleDetails.map((detail) => (
              <span
                key={detail}
                className="text-xs leading-4 text-[var(--theme-text-secondary)]"
                data-composer-skill-visible-detail={detail}
              >
                {detail}
              </span>
            ))}
          </span>
        ) : (
          <span key={selection.id}>{chip}</span>
        );
      })}
    </div>
  );
}
