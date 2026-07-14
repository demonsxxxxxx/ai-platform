import { Boxes, FileText, Sparkles, Wrench, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import type {
  ComposerSelection,
  ComposerSelectionKind,
} from "./composerSelections";
import { LibreChatComposerChip } from "../../librechat-ui/Chips";

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

function TaskSelectedSkillChip({
  selection,
  onRemove,
  removeLabel,
  statusLabel,
}: {
  selection: ComposerSelection;
  onRemove: (id: string) => void;
  removeLabel: string;
  statusLabel: string;
}) {
  const unavailable = selection.state === "unavailable";
  return (
    <span
      className={`inline-flex max-w-[220px] items-center gap-1.5 rounded-lg border py-0 pl-2.5 pr-0 text-xs ${
        unavailable
          ? "border-[var(--theme-warning-ring)] bg-[var(--theme-warning-soft)] text-[var(--theme-warning)]"
          : "border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)]"
      }`}
      data-librechat-composer-chip
      data-task-selected-skill-chip
      data-composer-chip-kind="skill"
      data-composer-chip-reference={selection.referenceId ?? selection.id}
      data-composer-chip-state={selection.state}
      title={selection.description}
    >
      <Sparkles size={13} className="shrink-0" />
      <span className="truncate font-medium">{selection.label}</span>
      {selection.state !== "enabled" ? (
        <span className="shrink-0 text-xs opacity-80">{statusLabel}</span>
      ) : null}
      <button
        type="button"
        onClick={() => onRemove(selection.id)}
        className="inline-flex size-11 shrink-0 items-center justify-center rounded-lg hover:bg-[var(--theme-sidebar-hover)] hover:text-[var(--theme-text)] sm:size-7"
        aria-label={removeLabel}
        data-task-selected-skill-remove
      >
        <X size={13} />
      </button>
    </span>
  );
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
        const chip = selection.kind === "skill" ? (
          <TaskSelectedSkillChip
            selection={selection}
            onRemove={onRemove}
            removeLabel={t("skillSelector.removeSelectedSkill", {
              name: selection.label,
              defaultValue: `Remove selected Skill ${selection.label}`,
            })}
            statusLabel={statusLabel}
          />
        ) : (
          <LibreChatComposerChip
            id={selection.id}
            kind={selection.kind}
            label={selection.label}
            state={selection.state}
            description={selection.description}
            referenceId={selection.referenceId}
            icon={Icon}
            statusLabel={statusLabel}
            removeLabel={t("common.remove")}
            onRemove={onRemove}
          />
        );
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
