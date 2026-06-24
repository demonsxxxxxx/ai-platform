import {
  Download,
  Filter,
  Plus,
  Search,
  Sparkles,
  Upload,
  UserRound,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { PanelHeader } from "../common/PanelHeader";
import { GovernanceAvailabilityBadge } from "../governance/GovernanceAvailabilityBadge";
import { resolveFrontendGovernanceState } from "../governance/frontendGovernanceState";
import { resolveGroupAvailability } from "../governance/groupAvailability";
import { WorkbenchStateSurface } from "../workbench/WorkbenchStateSurface";
import { workbenchSurface } from "../workbench/workbenchSurface";
import { useAuth } from "../../hooks/useAuth";
import { Permission } from "../../types";
import { PersonaEditorModal } from "./PersonaEditorModal";
import { PersonaPresetCard } from "./PersonaPresetCard";
import { PersonaScopeDropdown } from "./PersonaScopeDropdown";
import { PersonaTagFilterDropdown } from "./PersonaTagFilterDropdown";
import { usePersonaPlaza } from "./usePersonaPlaza";

export function PersonaWorkbenchPanel() {
  const { t } = useTranslation();
  const {
    isAuthenticated,
    isLoading: authLoading,
  } = useAuth();
  const persona = usePersonaPlaza();
  const governanceState = resolveFrontendGovernanceState({
    isAuthenticated,
    isLoading: authLoading || (persona.canRead && persona.isLoading),
    hasPermission: persona.canRead,
    projectionError: persona.error,
  });
  const readAvailability = resolveGroupAvailability({
    backed: true,
    enabled: governanceState === "ready" || governanceState === "degraded",
    adminOnly: governanceState === "forbidden",
  });
  const writeAvailability = resolveGroupAvailability({
    backed: true,
    enabled: persona.canWrite,
    adminOnly: !persona.canWrite,
  });
  const adminAvailability = resolveGroupAvailability({
    backed: true,
    enabled: persona.canAdmin,
    adminOnly: !persona.canAdmin,
  });

  if (governanceState === "loading" || governanceState === "forbidden") {
    return (
      <div
        data-persona-workbench-shell
        data-frontend-governance-state={governanceState}
        className="flex h-full min-h-0 items-center justify-center bg-[var(--theme-bg)] px-4"
      >
        <WorkbenchStateSurface
          state={governanceState}
          surface="persona-workbench"
          title={
            governanceState === "forbidden"
              ? t("personaPresets.forbiddenTitle", "角色工作台不可用")
              : t("workbench.states.loading.title")
          }
          description={
            governanceState === "forbidden"
              ? t(
                  "personaPresets.forbiddenDescription",
                  "当前账号缺少 {{permission}}，角色列表和管理操作会在前端 fail-closed。",
                  { permission: Permission.PERSONA_PRESET_READ },
                )
              : t("workbench.states.loading.description")
          }
          details={
            governanceState === "forbidden"
              ? [persona.error ?? Permission.PERSONA_PRESET_READ]
              : undefined
          }
        />
      </div>
    );
  }

  return (
    <div
      data-persona-workbench-shell
      data-frontend-governance-state={governanceState}
      className="flex h-full min-h-0 flex-col bg-[var(--theme-bg)] text-slate-950 dark:bg-stone-950 dark:text-stone-100"
    >
      <PanelHeader
        title={t("personaPresets.workbenchTitle", "角色工作台")}
        subtitle={t(
          "personaPresets.workbenchSubtitle",
          "选择、收藏、复制和维护对话角色；写入能力继续受后端权限约束。",
        )}
        icon={<UserRound size={20} className="text-slate-600" />}
        searchValue={persona.query}
        onSearchChange={persona.setQuery}
        searchPlaceholder={t("personaPresets.search", "搜索角色")}
        searchAccessory={
          <div className="flex shrink-0 items-center gap-1.5">
            <button
              ref={persona.scopeBtnRef}
              type="button"
              onClick={() => persona.setIsScopeOpen(true)}
              className="btn-secondary h-10 px-3 text-xs"
            >
              <Sparkles size={14} />
              {persona.scopeTabs.find((tab) => tab.key === persona.scopeFilter)
                ?.label ?? t("personaPresets.all", "全部")}
            </button>
            <button
              ref={persona.tagBtnRef}
              type="button"
              onClick={() => persona.setIsFilterOpen(true)}
              className="btn-secondary h-10 px-3 text-xs"
            >
              <Filter size={14} />
              {persona.activeTag ?? t("personaPresets.tags", "标签")}
            </button>
          </div>
        }
        actions={
          <div className="flex flex-wrap items-center justify-end gap-1.5">
            <GovernanceAvailabilityBadge
              state={readAvailability.state}
              labelKey={readAvailability.labelKey}
            />
            <GovernanceAvailabilityBadge
              state={writeAvailability.state}
              labelKey={writeAvailability.labelKey}
            />
            <button
              type="button"
              onClick={persona.handleExport}
              disabled={!persona.canRead || persona.isMutating}
              className="btn-secondary h-9 px-3 text-xs disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Download size={14} />
              {t("common.export", "导出")}
            </button>
            <button
              type="button"
              onClick={persona.handleImport}
              disabled={!persona.canWrite || persona.isImporting}
              className="btn-secondary h-9 px-3 text-xs disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Upload size={14} />
              {persona.isImporting
                ? t("common.importing", "导入中...")
                : t("common.import", "导入")}
            </button>
            <button
              type="button"
              onClick={() => persona.openModal(null, "user")}
              disabled={!persona.canWrite || persona.isMutating}
              className="btn-primary h-9 px-3 text-xs disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Plus size={14} />
              {t("personaPresets.createMine", "新建我的角色")}
            </button>
          </div>
        }
      />

      <div className="grid gap-3 px-4 pb-3 lg:grid-cols-3">
        <StatusTile
          title={t("personaPresets.readContract", "读取合同")}
          description={t(
            "personaPresets.readContractDescription",
            "使用 persona preset 公共接口读取当前可见角色。",
          )}
          availability={readAvailability}
        />
        <StatusTile
          title={t("personaPresets.writeContract", "写入合同")}
          description={t(
            "personaPresets.writeContractDescription",
            "创建、导入和编辑仅在账号具备写权限时开放。",
          )}
          availability={writeAvailability}
        />
        <StatusTile
          title={t("personaPresets.adminContract", "官方角色治理")}
          description={t(
            "personaPresets.adminContractDescription",
            "官方角色发布、归档和删除继续由管理员权限控制。",
          )}
          availability={adminAvailability}
        />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4">
        {governanceState === "degraded" ? (
          <WorkbenchStateSurface
            state="degraded"
            surface="persona-workbench"
            title={t("personaPresets.degradedTitle", "角色投影已降级")}
            description={t(
              "personaPresets.degradedDescription",
              "后端返回了非权限类错误；页面保留工作台结构并显示可用本地状态。",
            )}
            details={persona.error ? [persona.error] : undefined}
            className="mb-3 max-w-none text-left"
          />
        ) : null}

        {persona.paged.length === 0 ? (
          <div className={`${workbenchSurface.stateSurface} mx-auto mt-8 max-w-xl`}>
            <div className={workbenchSurface.stateIcon}>
              <Search size={20} />
            </div>
            <h2 className="mt-4 text-base font-semibold text-slate-900 dark:text-stone-100">
              {persona.hasActiveFilters
                ? t("personaPresets.noResults", "没有匹配的角色")
                : t("personaPresets.empty", "暂无角色预设")}
            </h2>
            <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-slate-600 dark:text-stone-300">
              {persona.hasActiveFilters
                ? t(
                    "personaPresets.tryDifferentFilters",
                    "调整搜索词、范围或标签后再试。",
                  )
                : t(
                    "personaPresets.emptyDescription",
                    "当前工作区还没有可见角色；具备写权限的用户可以新建我的角色。",
                  )}
            </p>
            {persona.hasActiveFilters ? (
              <button
                type="button"
                onClick={persona.clearFilters}
                className="btn-secondary mt-4"
              >
                {t("personaPresets.clearFilters", "清除筛选")}
              </button>
            ) : null}
          </div>
        ) : (
          <div className="grid auto-grid-cols gap-3">
            {persona.paged.map((preset) => (
              <PersonaPresetCard
                key={preset.id}
                preset={preset}
                selected={persona.selectedPresetId === preset.id}
                activeTag={persona.activeTag}
                canWrite={persona.canWrite}
                canAdmin={persona.canAdmin}
                onUse={persona.handleUse}
                onClear={persona.handleClear}
                onCopy={persona.handleCopy}
                onTogglePreference={persona.handleTogglePreference}
                onEdit={(item) => persona.openModal(item, item.scope)}
                onDelete={persona.setDeleteTarget}
                onToggleTag={persona.toggleTag}
              />
            ))}
          </div>
        )}
      </div>

      <input
        ref={persona.importInputRef}
        type="file"
        accept="application/json"
        className="hidden"
        onChange={persona.handleImportFile}
      />

      <PersonaScopeDropdown
        isOpen={persona.isScopeOpen}
        scopeFilter={persona.scopeFilter}
        scopeTabs={persona.scopeTabs}
        scopeBtnRef={persona.scopeBtnRef}
        onSelect={persona.handleScopeSelect}
        onClose={() => persona.setIsScopeOpen(false)}
      />
      <PersonaTagFilterDropdown
        isOpen={persona.isFilterOpen}
        allTags={persona.allTags}
        activeTag={persona.activeTag}
        hasActiveFilters={persona.hasActiveFilters}
        tagBtnRef={persona.tagBtnRef}
        onToggleTag={persona.toggleTag}
        onClearFilters={persona.clearFilters}
        onClose={() => persona.setIsFilterOpen(false)}
      />
      <PersonaEditorModal
        showModal={persona.showModal}
        editingPreset={persona.editingPreset}
        editorScope={persona.editorScope}
        canAdmin={persona.canAdmin}
        isMutating={persona.isMutating}
        createPreset={persona.createPreset}
        updatePreset={persona.updatePreset}
        onClose={persona.closeModal}
      />
      {persona.deleteTarget ? (
        <div className="fixed inset-0 z-[260] flex items-center justify-center bg-[var(--theme-overlay)] px-4">
          <section className={`${workbenchSurface.panel} w-full max-w-md p-4`}>
            <h2 className="text-base font-semibold text-slate-900 dark:text-stone-100">
              {t("personaPresets.deleteConfirmTitle", "删除角色")}
            </h2>
            <p className="mt-2 text-sm leading-6 text-slate-600 dark:text-stone-300">
              {t(
                "personaPresets.deleteConfirmDescription",
                "确定删除「{{name}}」？此操作会交给后端权限和审计继续校验。",
                { name: persona.deleteTarget.name },
              )}
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                className="btn-secondary"
                onClick={() => persona.setDeleteTarget(null)}
              >
                {t("common.cancel", "取消")}
              </button>
              <button
                type="button"
                className="btn-danger"
                disabled={persona.isDeleting}
                onClick={persona.handleDelete}
              >
                {t("common.delete", "删除")}
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}

function StatusTile({
  title,
  description,
  availability,
}: {
  title: string;
  description: string;
  availability: ReturnType<typeof resolveGroupAvailability>;
}) {
  return (
    <div className={`${workbenchSurface.compactPanel} p-3`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-slate-900 dark:text-stone-100">
            {title}
          </h3>
          <p className="mt-1 text-xs leading-5 text-slate-500 dark:text-stone-400">
            {description}
          </p>
        </div>
        <GovernanceAvailabilityBadge
          state={availability.state}
          labelKey={availability.labelKey}
        />
      </div>
    </div>
  );
}
