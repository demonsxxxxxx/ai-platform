import { Bot, Boxes, FileText, Sparkles, Wrench } from "lucide-react";
import { useTranslation } from "react-i18next";
import type {
  ComposerSelection,
  ComposerSelectionKind,
} from "./composerSelections";
import { LibreChatComposerChip } from "../../librechat-ui/Chips";

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
        return (
          <LibreChatComposerChip
            key={selection.id}
            id={selection.id}
            kind={selection.kind}
            label={selection.label}
            state={selection.state}
            description={selection.description}
            referenceId={selection.referenceId}
            icon={Icon}
            statusLabel={t(`composerChip.status.${selection.state}`, selection.state)}
            removeLabel={t("common.remove")}
            onRemove={onRemove}
          />
        );
      })}
    </div>
  );
}
