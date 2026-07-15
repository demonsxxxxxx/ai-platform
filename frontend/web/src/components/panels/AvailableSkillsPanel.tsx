import { FileText, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useSkills } from "../../hooks/useSkills";
import { PanelHeader } from "../common/PanelHeader";
import { workbenchSurface } from "../workbench/workbenchSurface";
import { projectOrdinarySkillCatalogItem } from "./ordinaryCatalogPolicy";

/** Read-only catalog used by ordinary company accounts. */
export function AvailableSkillsPanel() {
  const { t } = useTranslation();
  const { skills, isLoading, error } = useSkills();
  const catalog = skills
    .map((skill) =>
      projectOrdinarySkillCatalogItem({
        name: skill.name,
        description: skill.description,
        inputModes: skill.input_modes,
      }),
    )
    .filter((skill) => skill.displayName.length > 0);

  return (
    <div
      className={workbenchSurface.page}
      data-ordinary-skills-catalog
    >
      <PanelHeader
        title={t("skills.available.title")}
        subtitle={t("skills.available.subtitle")}
        icon={<Sparkles size={20} className="text-theme-text-secondary" />}
      />
      <div className={workbenchSurface.catalog.content}>
        {isLoading ? (
          <div className={workbenchSurface.catalog.emptyState}>
            <p className={workbenchSurface.catalog.emptyTitle}>
              {t("skills.available.loading")}
            </p>
          </div>
        ) : error ? (
          <div className={workbenchSurface.catalog.emptyState}>
            <p className={workbenchSurface.catalog.emptyTitle}>
              {t("skills.available.unavailable")}
            </p>
          </div>
        ) : catalog.length === 0 ? (
          <div className={workbenchSurface.catalog.emptyState}>
            <p className={workbenchSurface.catalog.emptyTitle}>
              {t("skills.available.empty")}
            </p>
          </div>
        ) : (
          <div className={workbenchSurface.catalog.cardGrid}>
            {catalog.map((skill) => (
              <article
                key={skill.displayName}
                className={workbenchSurface.catalog.entryCard}
              >
                <div className="flex items-start gap-3">
                  <div className={workbenchSurface.catalog.compactIconBox}>
                    <FileText size={16} />
                  </div>
                  <div className="min-w-0">
                    <h2 className={workbenchSurface.catalog.title}>
                      {skill.displayName}
                    </h2>
                    <p className={`mt-1 ${workbenchSurface.catalog.body}`}>
                      {skill.description || t("skills.noDescription")}
                    </p>
                  </div>
                </div>

                {skill.applicableFileTypes.length > 0 ? (
                  <div className="mt-4">
                    <p className={workbenchSurface.catalog.label}>
                      {t("skills.available.fileTypes")}
                    </p>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {skill.applicableFileTypes.map((fileType) => (
                        <span
                          key={fileType}
                          className="rounded-md bg-[var(--theme-bg-sidebar)] px-2 py-1 text-xs text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]"
                        >
                          {fileType}
                        </span>
                      ))}
                    </div>
                  </div>
                ) : null}
              </article>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
