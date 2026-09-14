import {
  Download,
  Archive,
  Boxes,
  Code2,
  FileText,
  FileType2,
  Globe2,
  Package2,
  Presentation,
  Pencil,
  Power,
  MoreHorizontal,
  Search,
  Sheet,
  Store,
  UploadCloud,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
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
  canPublish: boolean;
  canToggle: boolean;
  onDelete: (name: string) => void;
  onEdit: (skill: NonNullable<SkillCatalogEntry["runtimeSkill"]>) => void;
  onExportZip: (name: string) => void;
  onSelectDetail: (skillId: string) => void;
  onSelectAll: () => void;
  onSelectSkill: (name: string) => void;
  onToggle: (name: string) => void;
  onUploadVersion: (skillName: string) => void;
  selectedNames: Set<string>;
  selectedSkillId: string | null;
  entries: SkillCatalogEntry[];
}

function catalogStatusKey(status: SkillCatalogStatus): string {
  if (status === "available") return "skills.managementTable.distributed";
  if (status === "hidden") return "skills.managementTable.hidden";
  if (status === "disabled") return "skills.managementTable.distributionDisabled";
  if (status === "internal") return "skills.managementTable.internal";
  return "skills.managementTable.notPublished";
}

function updatedDateLabel(value?: string): string {
  if (!value) return "-";
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp)
    ? new Date(timestamp).toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      })
    : "-";
}

function skillIcon(name: string) {
  const normalized = name.toLocaleLowerCase();
  if (normalized.includes("spreadsheet") || normalized.includes("excel")) return Sheet;
  if (normalized === "pdf" || normalized.includes("pdf")) return FileType2;
  if (normalized.includes("presentation") || normalized.includes("slide")) return Presentation;
  if (normalized.includes("research")) return Search;
  if (normalized.includes("web") || normalized.includes("browser")) return Globe2;
  if (normalized.includes("code") || normalized.includes("interpreter")) return Code2;
  if (normalized.includes("document") || normalized.includes("word")) return FileText;
  return Package2;
}

export function SkillManagementTable({
  canBatch,
  canDelete,
  canEdit,
  canExport,
  canPublish,
  canToggle,
  onDelete,
  onEdit,
  onExportZip,
  onSelectDetail,
  onSelectAll,
  onSelectSkill,
  onToggle,
  onUploadVersion,
  selectedNames,
  selectedSkillId,
  entries,
}: SkillManagementTableProps) {
  const { t } = useTranslation();
  const [openActionName, setOpenActionName] = useState<string | null>(null);
  const openActionMenuRef = useRef<HTMLDivElement>(null);
  const selectableEntries = entries.filter((entry) => entry.actionName !== null);
  const allSelectableSelected =
    selectableEntries.length > 0 &&
    selectableEntries.every((entry) => selectedNames.has(entry.actionName!));

  useEffect(() => {
    if (!openActionName) return;

    const closeMenu = (event: MouseEvent) => {
      if (
        openActionMenuRef.current &&
        !openActionMenuRef.current.contains(event.target as Node)
      ) {
        setOpenActionName(null);
      }
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpenActionName(null);
    };

    document.addEventListener("mousedown", closeMenu);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeMenu);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [openActionName]);

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
        {canBatch ? (
          <span className="skill-management-table__select">
            <input
              aria-label={
                allSelectableSelected
                  ? t("common.deselectAll")
                  : t("common.selectAll")
              }
              checked={allSelectableSelected}
              onChange={onSelectAll}
              type="checkbox"
            />
          </span>
        ) : null}
        <span role="columnheader">{t("skills.managementTable.skill")}</span>
        <span role="columnheader">{t("skills.managementTable.runtimeStatus")}</span>
        <span className="skill-management-table__distribution" role="columnheader">{t("skills.managementTable.catalogStatus")}</span>
        <span className="skill-management-table__package" role="columnheader">{t("skills.managementTable.package")}</span>
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
          const rowCanPublish = canPublish && canAct;
          const rowCanDelete = canDelete && canAct;
          const hasActions =
            rowCanToggle || rowCanEdit || rowCanExport || rowCanPublish || rowCanDelete;
          const CatalogStatusIcon =
            entry.catalogStatus === "internal" ? Boxes : Store;
          const SkillIcon = skillIcon(entry.displayName);
          return (
            <div
            aria-selected={entry.id === selectedSkillId}
            className={`skill-management-table__row ${canBatch ? "skill-management-table__row--selectable" : ""} ${entry.id === selectedSkillId ? "skill-management-table__row--selected" : ""}`}
            data-catalog-status={entry.catalogStatus}
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
              <span className="skill-management-table__icon" aria-hidden="true">
                <SkillIcon size={18} />
              </span>
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

            <div className="skill-management-table__runtime" data-label={t("skills.managementTable.runtimeStatus")} role="cell">
              <span
                className={`skill-management-table__status ${
                  entry.runtimeEnabled
                    ? "skill-management-table__status--active"
                    : entry.catalogStatus === "internal"
                      ? "skill-management-table__status--internal"
                      : ""
                }`}
              >
                <span aria-hidden="true" />
                {entry.catalogStatus === "internal"
                  ? t("skills.managementTable.internalRuntime")
                  : entry.runtimeEnabled === null
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
                className={`skill-management-table__status skill-management-table__status--catalog skill-management-table__status--${entry.catalogStatus}`}
              >
                <CatalogStatusIcon aria-hidden="true" size={13} />
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
              className="skill-management-table__package"
              data-label={t("skills.managementTable.package")}
              role="cell"
            >
              <span className="font-mono text-xs text-[var(--theme-text)]">
                {entry.version || "-"}
              </span>
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
              {rowCanPublish ? (
                <button
                  aria-label={t("skills.managementTable.updateVersionSkill", {
                    name: entry.displayName,
                  })}
                  className="btn-icon"
                  onClick={(event) => {
                    event.stopPropagation();
                    onUploadVersion(actionName);
                  }}
                  title={t("skills.managementTable.updateVersion")}
                  type="button"
                >
                  <UploadCloud aria-hidden="true" size={16} />
                </button>
              ) : null}
              {hasActions ? (
                <div
                  className="skill-management-table__action-menu"
                  ref={openActionName === actionName ? openActionMenuRef : undefined}
                >
                  <button
                    aria-expanded={openActionName === actionName}
                    aria-haspopup="menu"
                    aria-label={t("common.menu")}
                    className="btn-icon"
                    onClick={(event) => {
                      event.stopPropagation();
                      setOpenActionName((current) =>
                        current === actionName ? null : actionName,
                      );
                    }}
                    title={t("common.menu")}
                    type="button"
                  >
                    <MoreHorizontal aria-hidden="true" size={17} />
                  </button>
                  {openActionName === actionName ? (
                    <div
                      aria-label={`${entry.displayName} ${t("common.menu")}`}
                      className="skill-management-table__action-menu-panel"
                      onClick={(event) => event.stopPropagation()}
                      role="menu"
                    >
                      {rowCanToggle ? (
                        <button
                          onClick={() => {
                            setOpenActionName(null);
                            onToggle(actionName);
                          }}
                          role="menuitem"
                          type="button"
                        >
                          <Power aria-hidden="true" size={14} />
                          {t(
                            entry.runtimeEnabled
                              ? "skills.managementTable.disable"
                              : "skills.managementTable.enable",
                          )}
                        </button>
                      ) : null}
                      {rowCanEdit ? (
                        <button
                          onClick={() => {
                            setOpenActionName(null);
                            onEdit(entry.runtimeSkill!);
                          }}
                          role="menuitem"
                          type="button"
                        >
                          <Pencil aria-hidden="true" size={14} />
                          {t("skills.managementTable.edit")}
                        </button>
                      ) : null}
                      {rowCanExport ? (
                        <button
                          onClick={() => {
                            setOpenActionName(null);
                            onExportZip(actionName);
                          }}
                          role="menuitem"
                          type="button"
                        >
                          <Download aria-hidden="true" size={14} />
                          {t("skills.exportZip")}
                        </button>
                      ) : null}
                      {rowCanDelete ? (
                        <button
                          className="skill-management-table__archive-action"
                          onClick={() => {
                            setOpenActionName(null);
                            onDelete(actionName);
                          }}
                          role="menuitem"
                          type="button"
                        >
                          <Archive aria-hidden="true" size={14} />
                          {t("skills.managementTable.delete")}
                        </button>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              ) : null}
              {!hasActions ? (
                <span className="text-xs text-[var(--theme-text-secondary)]">
                  {t(
                    entry.catalogStatus === "internal"
                      ? "skills.managementTable.internalDependency"
                      : "skills.managementTable.readOnly",
                  )}
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
