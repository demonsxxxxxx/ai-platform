import {
  Download,
  Archive,
  FileArchive,
  Pencil,
  Power,
  Store,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import type {
  SkillCatalogEntry,
  SkillCatalogStatus,
} from "./skillCatalogEntries";

const INTERACTIVE_ROW_TARGET =
  'button, a, input, select, textarea, [role="button"], [role="link"], [role="checkbox"], [contenteditable="true"]';

function isInteractiveRowTarget(
  target: EventTarget | null,
  currentTarget: EventTarget | null,
): boolean {
  return (
    target !== currentTarget &&
    target instanceof Element &&
    target.closest(INTERACTIVE_ROW_TARGET) !== null
  );
}

interface SkillManagementTableProps {
  canBatch: boolean;
  canDelete: boolean;
  canEdit: boolean;
  canExport: boolean;
  canToggle: boolean;
  onDelete: (name: string) => void;
  onEdit: (skill: NonNullable<SkillCatalogEntry["runtimeSkill"]>) => void;
  onExportZip: (name: string) => void;
  onSelectDetail: (skillId: string) => void;
  onSelectSkill: (name: string) => void;
  onToggle: (name: string) => void;
  selectedNames: Set<string>;
  selectedSkillId: string | null;
  entries: SkillCatalogEntry[];
}

function catalogStatusKey(status: SkillCatalogStatus): string {
  if (status === "available") return "skills.managementTable.distributed";
  if (status === "hidden") return "skills.managementTable.hidden";
  if (status === "disabled") return "skills.managementTable.distributionDisabled";
  return "skills.managementTable.notPublished";
}

function updatedDateLabel(value?: string): string {
  if (!value) return "-";
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? new Date(timestamp).toLocaleDateString() : "-";
}

export function SkillManagementTable({
  canBatch,
  canDelete,
  canEdit,
  canExport,
  canToggle,
  onDelete,
  onEdit,
  onExportZip,
  onSelectDetail,
  onSelectSkill,
  onToggle,
  selectedNames,
  selectedSkillId,
  entries,
}: SkillManagementTableProps) {
  const { t } = useTranslation();

  return (
    <div
      aria-label={t("skills.managementTable.listLabel")}
      className="skill-management-table skill-management-table--master"
      data-skill-management-table
      role="table"
    >
      <div
        className={`skill-management-table__head ${canBatch ? "skill-management-table__head--selectable" : ""}`}
        role="row"
      >
        {canBatch ? <span aria-hidden="true" /> : null}
        <span role="columnheader">{t("skills.managementTable.skill")}</span>
        <span className="skill-management-table__package" role="columnheader">{t("skills.managementTable.package")}</span>
        <span role="columnheader">{t("skills.managementTable.runtimeStatus")}</span>
        <span className="skill-management-table__distribution" role="columnheader">{t("skills.managementTable.catalogStatus")}</span>
        <span className="skill-management-table__updated" role="columnheader">{t("skills.managementTable.updatedAt")}</span>
        <span aria-label={t("skills.managementTable.actions")} role="columnheader" />
      </div>

      <div role="rowgroup">
        {entries.map((entry) => {
          const actionName = entry.actionName;
          const canAct = actionName !== null && entry.runtimeSkill !== null;
          const rowCanToggle = canToggle && canAct;
          const rowCanEdit = canEdit && canAct;
          const rowCanExport = canExport && canAct;
          const rowCanDelete = canDelete && canAct;
          const hasActions =
            rowCanToggle || rowCanEdit || rowCanExport || rowCanDelete;
          return (
            <div
            aria-selected={entry.id === selectedSkillId}
            className={`skill-management-table__row ${canBatch ? "skill-management-table__row--selectable" : ""} ${entry.id === selectedSkillId ? "skill-management-table__row--selected" : ""}`}
            data-skill-catalog-item={entry.id}
            key={entry.id}
            onClick={() => onSelectDetail(entry.id)}
            onKeyDown={(event) => {
              if (isInteractiveRowTarget(event.target, event.currentTarget)) {
                return;
              }
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onSelectDetail(entry.id);
              }
            }}
            role="row"
            tabIndex={0}
          >
            {canBatch && actionName ? (
              <div className="skill-management-table__select" role="cell">
                <input
                  aria-label={t("skills.managementTable.selectSkill", { name: entry.displayName })}
                  checked={selectedNames.has(actionName)}
                  onClick={(event) => event.stopPropagation()}
                  onChange={() => onSelectSkill(actionName)}
                  type="checkbox"
                />
              </div>
            ) : canBatch ? (
              <span aria-hidden="true" role="cell" />
            ) : null}

            <div
              className="skill-management-table__identity"
              data-label={t("skills.managementTable.skill")}
              role="cell"
            >
              <div className="min-w-0">
                <p
                  className="truncate text-sm font-semibold text-[var(--theme-text)]"
                  title={entry.displayName}
                >
                  {entry.displayName}
                </p>
                <p
                  className="mt-0.5 line-clamp-2 text-xs leading-5 text-[var(--theme-text-secondary)]"
                  title={entry.description || undefined}
                >
                  {entry.description || t("skills.noDescription")}
                </p>
                {entry.tags.length > 0 ? (
                  <div
                    aria-label={t("skills.managementTable.tags")}
                    className="mt-1.5 flex max-w-full gap-1 overflow-hidden"
                  >
                    {entry.tags.slice(0, 2).map((tag) => (
                      <span
                        className="skill-management-table__tag"
                        key={tag}
                        title={tag}
                      >
                        {tag}
                      </span>
                    ))}
                    {entry.tags.length > 2 ? (
                      <span className="skill-management-table__tag">
                        +{entry.tags.length - 2}
                      </span>
                    ) : null}
                  </div>
                ) : null}
              </div>
            </div>

            <div
              className="skill-management-table__package"
              data-label={t("skills.managementTable.package")}
              role="cell"
            >
              <span className="inline-flex items-center gap-1.5 font-mono text-xs text-[var(--theme-text)]">
                <FileArchive aria-hidden="true" size={14} />
                {entry.version || "-"}
              </span>
              <span className="mt-1 block text-[11px] text-[var(--theme-text-secondary)]">
                {entry.fileCount === null
                  ? t("skills.managementTable.packageOnly")
                  : t("skills.managementTable.fileCount", { count: entry.fileCount })}
              </span>
            </div>

            <div className="skill-management-table__runtime" data-label={t("skills.managementTable.runtimeStatus")} role="cell">
              <span
                className={`skill-management-table__status ${entry.runtimeEnabled ? "skill-management-table__status--active" : ""}`}
              >
                <span aria-hidden="true" />
                {entry.runtimeEnabled === null
                  ? t("skills.managementTable.notPublished")
                  : entry.runtimeEnabled
                  ? t("skills.managementTable.enabled")
                  : t("skills.managementTable.disabled")}
              </span>
            </div>

            <div
              className="skill-management-table__distribution min-w-0"
              data-label={t("skills.managementTable.catalogStatus")}
              role="cell"
            >
              <span
                className={`skill-management-table__status ${entry.catalogStatus === "available" ? "skill-management-table__status--distribution" : ""}`}
              >
                <Store aria-hidden="true" size={13} />
                {t(catalogStatusKey(entry.catalogStatus))}
              </span>
              {entry.publishedCatalogName ? (
                <span
                  className="mt-1 block truncate text-[11px] text-[var(--theme-text-secondary)]"
                  title={entry.publishedCatalogName}
                >
                  {entry.publishedCatalogName}
                </span>
              ) : null}
            </div>

            <div
              className="skill-management-table__updated text-xs text-[var(--theme-text-secondary)]"
              data-label={t("skills.managementTable.updatedAt")}
              role="cell"
            >
              {updatedDateLabel(entry.updatedAt ?? undefined)}
            </div>

            <div
              className="skill-management-table__actions"
              data-label={t("skills.managementTable.actions")}
              role="cell"
            >
              {rowCanToggle ? (
                <button
                  aria-label={t(
                    entry.runtimeEnabled
                      ? "skills.managementTable.disableSkill"
                      : "skills.managementTable.enableSkill",
                    { name: entry.displayName },
                  )}
                  className="btn-icon"
                  onClick={(event) => {
                    event.stopPropagation();
                    onToggle(actionName);
                  }}
                  title={t(
                    entry.runtimeEnabled
                      ? "skills.managementTable.disable"
                      : "skills.managementTable.enable",
                  )}
                  type="button"
                >
                  <Power aria-hidden="true" size={16} />
                </button>
              ) : null}
              {rowCanEdit ? (
                <button
                  aria-label={t("skills.managementTable.editSkill", { name: entry.displayName })}
                  className="btn-icon"
                  onClick={(event) => {
                    event.stopPropagation();
                    onEdit(entry.runtimeSkill!);
                  }}
                  title={t("skills.managementTable.edit")}
                  type="button"
                >
                  <Pencil aria-hidden="true" size={16} />
                </button>
              ) : null}
              {rowCanExport ? (
                <button
                  aria-label={t("skills.managementTable.exportSkill", { name: entry.displayName })}
                  className="btn-icon"
                  onClick={(event) => {
                    event.stopPropagation();
                    onExportZip(actionName);
                  }}
                  title={t("skills.exportZip")}
                  type="button"
                >
                  <Download aria-hidden="true" size={16} />
                </button>
              ) : null}
              {rowCanDelete ? (
                <button
                  aria-label={t("skills.managementTable.deleteSkill", { name: entry.displayName })}
                  className="btn-icon skill-management-table__archive-action"
                  onClick={(event) => {
                    event.stopPropagation();
                    onDelete(actionName);
                  }}
                  title={t("skills.managementTable.delete")}
                  type="button"
                >
                  <Archive aria-hidden="true" size={16} />
                </button>
              ) : null}
              {!hasActions ? (
                <span className="text-xs text-[var(--theme-text-secondary)]">
                  {t("skills.managementTable.readOnly")}
                </span>
              ) : null}
            </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
