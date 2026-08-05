import {
  Download,
  FileArchive,
  Pencil,
  Power,
  Store,
  Trash2,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import type { SkillResponse } from "../../../types";

interface SkillManagementTableProps {
  canBatch: boolean;
  canDelete: boolean;
  canEdit: boolean;
  canExport: boolean;
  canToggle: boolean;
  onDelete: (name: string) => void;
  onEdit: (skill: SkillResponse) => void;
  onExportZip: (name: string) => void;
  onSelectSkill: (name: string) => void;
  onToggle: (name: string) => void;
  selectedNames: Set<string>;
  skills: SkillResponse[];
}

function tenantDistributionLabel(skill: SkillResponse): string {
  if (skill.marketplace_is_active) return "租户分发中";
  if (skill.is_published) return "租户分发已停用";
  return "未进入租户目录";
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
  onSelectSkill,
  onToggle,
  selectedNames,
  skills,
}: SkillManagementTableProps) {
  const { t } = useTranslation();
  const hasActions = canToggle || canEdit || canExport || canDelete;

  return (
    <div
      aria-label="Skill 管理列表"
      className="skill-management-table"
      data-skill-management-table
      role="table"
    >
      <div
        className={`skill-management-table__head ${canBatch ? "skill-management-table__head--selectable" : ""}`}
        role="row"
      >
        {canBatch ? <span aria-hidden="true" /> : null}
        <span role="columnheader">Skill</span>
        <span role="columnheader">版本包</span>
        <span role="columnheader">运行状态</span>
        <span role="columnheader">租户分发</span>
        <span role="columnheader">更新时间</span>
        <span aria-label="操作" role="columnheader" />
      </div>

      <div role="rowgroup">
        {skills.map((skill) => (
          <div
            className={`skill-management-table__row ${canBatch ? "skill-management-table__row--selectable" : ""}`}
            key={skill.name}
            role="row"
          >
            {canBatch ? (
              <div className="skill-management-table__select" role="cell">
                <input
                  aria-label={`选择 ${skill.name}`}
                  checked={selectedNames.has(skill.name)}
                  onChange={() => onSelectSkill(skill.name)}
                  type="checkbox"
                />
              </div>
            ) : null}

            <div
              className="skill-management-table__identity"
              data-label="Skill"
              role="cell"
            >
              <div className="min-w-0">
                <p
                  className="truncate text-sm font-semibold text-[var(--theme-text)]"
                  title={skill.name}
                >
                  {skill.name}
                </p>
                <p
                  className="mt-0.5 line-clamp-2 text-xs leading-5 text-[var(--theme-text-secondary)]"
                  title={skill.description || undefined}
                >
                  {skill.description || t("skills.noDescription")}
                </p>
                {skill.tags.length > 0 ? (
                  <div
                    aria-label="标签"
                    className="mt-1.5 flex max-w-full gap-1 overflow-hidden"
                  >
                    {skill.tags.slice(0, 2).map((tag) => (
                      <span
                        className="skill-management-table__tag"
                        key={tag}
                        title={tag}
                      >
                        {tag}
                      </span>
                    ))}
                    {skill.tags.length > 2 ? (
                      <span className="skill-management-table__tag">
                        +{skill.tags.length - 2}
                      </span>
                    ) : null}
                  </div>
                ) : null}
              </div>
            </div>

            <div
              className="skill-management-table__package"
              data-label="版本包"
              role="cell"
            >
              <span className="inline-flex items-center gap-1.5 font-mono text-xs text-[var(--theme-text)]">
                <FileArchive aria-hidden="true" size={14} />
                {skill.expected_version || "-"}
              </span>
              <span className="mt-1 block text-[11px] text-[var(--theme-text-secondary)]">
                {skill.file_count} 个文件
              </span>
            </div>

            <div data-label="运行状态" role="cell">
              <span
                className={`skill-management-table__status ${skill.enabled ? "skill-management-table__status--active" : ""}`}
              >
                <span aria-hidden="true" />
                {skill.enabled ? "已启用" : "已停用"}
              </span>
            </div>

            <div className="min-w-0" data-label="租户分发" role="cell">
              <span
                className={`skill-management-table__status ${skill.marketplace_is_active ? "skill-management-table__status--distribution" : ""}`}
              >
                <Store aria-hidden="true" size={13} />
                {tenantDistributionLabel(skill)}
              </span>
              {skill.published_marketplace_name ? (
                <span
                  className="mt-1 block truncate text-[11px] text-[var(--theme-text-secondary)]"
                  title={skill.published_marketplace_name}
                >
                  {skill.published_marketplace_name}
                </span>
              ) : null}
            </div>

            <div
              className="text-xs text-[var(--theme-text-secondary)]"
              data-label="更新时间"
              role="cell"
            >
              {updatedDateLabel(skill.updated_at)}
            </div>

            <div className="skill-management-table__actions" role="cell">
              {canToggle ? (
                <button
                  aria-label={skill.enabled ? `停用 ${skill.name}` : `启用 ${skill.name}`}
                  className="btn-icon"
                  onClick={() => onToggle(skill.name)}
                  title={skill.enabled ? "停用" : "启用"}
                  type="button"
                >
                  <Power aria-hidden="true" size={16} />
                </button>
              ) : null}
              {canEdit ? (
                <button
                  aria-label={`编辑 ${skill.name}`}
                  className="btn-icon"
                  onClick={() => onEdit(skill)}
                  title="编辑"
                  type="button"
                >
                  <Pencil aria-hidden="true" size={16} />
                </button>
              ) : null}
              {canExport ? (
                <button
                  aria-label={`导出 ${skill.name}`}
                  className="btn-icon"
                  onClick={() => onExportZip(skill.name)}
                  title="导出 ZIP"
                  type="button"
                >
                  <Download aria-hidden="true" size={16} />
                </button>
              ) : null}
              {canDelete ? (
                <button
                  aria-label={`删除 ${skill.name}`}
                  className="btn-icon text-[var(--theme-danger)]"
                  onClick={() => onDelete(skill.name)}
                  title="删除"
                  type="button"
                >
                  <Trash2 aria-hidden="true" size={16} />
                </button>
              ) : null}
              {!hasActions ? (
                <span className="text-xs text-[var(--theme-text-secondary)]">只读</span>
              ) : null}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
