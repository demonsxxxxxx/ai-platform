import { useEffect, useMemo, useState } from "react";
import {
  DatabaseZap,
  Gauge,
  LockKeyhole,
  Search,
  ShieldCheck,
  SlidersHorizontal,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { ModelIconImg } from "../agent/modelIcon.tsx";
import { PanelHeader } from "../common/PanelHeader";
import { PanelLoadingState } from "../common/PanelLoadingState";
import { GovernanceAvailabilityBadge } from "../governance/GovernanceAvailabilityBadge";
import { resolveGroupAvailability } from "../governance/groupAvailability";
import { WorkbenchStateSurface } from "../workbench/WorkbenchStateSurface";
import { workbenchSurface } from "../workbench/workbenchSurface";
import { modelPublicApi, type ModelOption } from "../../services/api/modelPublic";
import { useAuth } from "../../hooks/useAuth";
import { Permission } from "../../types";

type ProviderProjection = {
  value: string;
  protocol: string;
  prefixes: string[];
};

type ModelCatalogState = {
  models: ModelOption[];
  providers: ProviderProjection[];
  enabledCount: number;
  defaultModelId?: string | null;
};

function providerLabel(model: ModelOption): string {
  return model.provider || model.value.split("/")[0] || "custom";
}

function deriveProviderProjections(models: ModelOption[]): ProviderProjection[] {
  const providers = new Map<string, ProviderProjection>();

  for (const model of models) {
    const value = providerLabel(model);
    const prefix = model.value.includes("/")
      ? model.value.split("/")[0]
      : value;
    const existing = providers.get(value);
    if (existing) {
      if (!existing.prefixes.includes(prefix)) {
        existing.prefixes.push(prefix);
      }
      continue;
    }
    providers.set(value, {
      value,
      protocol: value,
      prefixes: [prefix],
    });
  }

  return Array.from(providers.values()).sort((left, right) =>
    left.value.localeCompare(right.value),
  );
}

function contextWindowLabel(model: ModelOption, fallback: string): string {
  const value = model.profile?.max_input_tokens;
  if (!value) return fallback;
  return `${value.toLocaleString()} tokens`;
}

function normalizeQuery(value: string): string {
  return value.trim().toLowerCase();
}

function modelMatchesQuery(model: ModelOption, query: string): boolean {
  if (!query) return true;
  return [model.label, model.value, model.provider, model.description]
    .filter(Boolean)
    .some((part) => part!.toLowerCase().includes(query));
}

/** Render the governed read-only model catalog from public model projections. */
export function ModelCatalogPanel() {
  const { t } = useTranslation();
  const { hasAnyPermission } = useAuth();
  const [searchQuery, setSearchQuery] = useState("");
  const [state, setState] = useState<ModelCatalogState | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const canAdminModels = hasAnyPermission([Permission.MODEL_ADMIN]);
  const adminAvailability = resolveGroupAvailability({
    adminOnly: canAdminModels,
  });
  const publicAvailability = resolveGroupAvailability({
    backed: true,
    enabled: Boolean(state?.models.length),
  });

  useEffect(() => {
    let cancelled = false;

    async function loadModelCatalog() {
      setIsLoading(true);
      setLoadError(null);
      try {
        const catalog = await modelPublicApi.listAvailable();
        if (cancelled) return;

        const models = catalog.models ?? [];
        setState({
          models,
          providers: deriveProviderProjections(models),
          enabledCount: catalog.enabled_count ?? 0,
          defaultModelId: catalog.default_model_id,
        });
      } catch (err) {
        if (cancelled) return;
        setState(null);
        setLoadError(
          err instanceof Error
            ? err.message
            : t("models.catalogLoadFailed", "模型目录暂不可用"),
        );
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }

    void loadModelCatalog();

    return () => {
      cancelled = true;
    };
  }, [t]);

  const query = normalizeQuery(searchQuery);
  const filteredModels = useMemo(
    () => (state?.models ?? []).filter((model) => modelMatchesQuery(model, query)),
    [query, state?.models],
  );
  const providerCount = state?.providers.length ?? 0;

  if (isLoading) {
    return (
      <div className="h-full bg-[var(--theme-workbench-canvas)]">
        <PanelLoadingState text={t("models.loading", "正在加载模型目录")} />
      </div>
    );
  }

  if (!state && loadError) {
    return (
      <div className="flex h-full min-h-0 items-center justify-center bg-[var(--theme-workbench-canvas)] px-4">
        <WorkbenchStateSurface
          state="degraded"
          surface="model-public-projection"
          title={t("models.catalogUnavailable", "模型目录暂不可用")}
          description={t(
            "models.catalogUnavailableDescription",
            "公开模型投影没有返回可用数据。聊天页会继续使用已缓存或默认模型，管理写操作保持关闭。",
          )}
        />
      </div>
    );
  }

  return (
    <div
      data-model-catalog-shell
      data-frontend-governance-state={
        state?.models.length ? "ready" : "degraded"
      }
      className="flex h-full min-h-0 flex-col bg-[var(--theme-workbench-canvas)] text-[var(--theme-text)]"
    >
      <PanelHeader
        title={t("models.title", "模型")}
        subtitle={t(
          "models.subtitle",
          "查看当前工作台可用模型，管理写操作按治理策略保持关闭。",
        )}
        icon={<DatabaseZap size={20} className="text-theme-text-secondary" />}
        searchValue={searchQuery}
        onSearchChange={setSearchQuery}
        searchPlaceholder={t("models.searchPlaceholder", "搜索模型、供应商或能力")}
      />

      <div className="px-4 pb-2 pt-3">
        <section className="grid gap-3 lg:grid-cols-3">
          <div className={workbenchSurface.compactPanel}>
            <div className="flex items-start justify-between gap-3 p-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <ShieldCheck size={16} className="text-stone-500" />
                  <h3 className="text-sm font-semibold text-[var(--theme-text)]">
                    {t("models.publicProjection", "公开模型投影")}
                  </h3>
                </div>
                <p className="mt-1 text-xs leading-5 text-stone-500 dark:text-stone-400">
                  {t(
                    "models.publicProjectionDescription",
                    "聊天和选择器只读取公开可用模型，不暴露密钥、网关配置或租户外信息。",
                  )}
                </p>
              </div>
              <GovernanceAvailabilityBadge
                state={publicAvailability.state}
                labelKey={publicAvailability.labelKey}
              />
            </div>
          </div>
          <div className={workbenchSurface.compactPanel}>
            <div className="flex items-start justify-between gap-3 p-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <Gauge size={16} className="text-stone-500" />
                  <h3 className="text-sm font-semibold text-[var(--theme-text)]">
                    {t("models.catalogStats", "目录状态")}
                  </h3>
                </div>
                <p className="mt-1 text-xs leading-5 text-stone-500 dark:text-stone-400">
                  {t("models.catalogStatsDescription", "已启用 {{enabled}} / {{total}} 个模型，{{providers}} 个供应商。", {
                    enabled: state?.enabledCount ?? 0,
                    total: state?.models.length ?? 0,
                    providers: providerCount,
                  })}
                </p>
              </div>
            </div>
          </div>
          <div
            data-model-admin-governance
            data-fail-closed-surface="model-admin-governance"
            className={workbenchSurface.compactPanel}
          >
            <div className="flex items-start justify-between gap-3 p-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <LockKeyhole size={16} className="text-stone-500" />
                  <h3 className="text-sm font-semibold text-[var(--theme-text)]">
                    {t("models.adminGovernance", "管理写操作")}
                  </h3>
                </div>
                <p className="mt-1 text-xs leading-5 text-stone-500 dark:text-stone-400">
                  {t(
                    "models.adminGovernanceDescription",
                    "新增、排序、密钥和网关配置只在具备 model:admin 权限的治理入口开放。",
                  )}
                </p>
              </div>
              <GovernanceAvailabilityBadge
                state={adminAvailability.state}
                labelKey={adminAvailability.labelKey}
              />
            </div>
          </div>
        </section>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {filteredModels.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center text-theme-text-secondary">
            {query ? (
              <Search size={42} className="mb-3 text-theme-text-secondary" />
            ) : (
              <SlidersHorizontal
                size={42}
                className="mb-3 text-theme-text-secondary"
              />
            )}
            <p className="text-center text-sm">
              {query
                ? t("models.noMatchingModels", "没有匹配的模型")
                : t("models.noModels", "暂无可用模型")}
            </p>
            <p className="mt-2 max-w-md text-center text-xs leading-5 text-stone-500 dark:text-stone-400">
              {t(
                "models.emptyDescription",
                "模型目录来自公开投影。若这里为空，聊天页会 fail-closed 到默认模型配置，并保留管理写操作锁定。",
              )}
            </p>
          </div>
        ) : (
          <div className="grid gap-3 lg:grid-cols-2 2xl:grid-cols-3">
            {filteredModels.map((model) => {
              const isDefault = model.id === state?.defaultModelId;
              return (
                <article
                  key={model.id || model.value}
                  className={`${workbenchSurface.compactPanel} p-4`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex min-w-0 items-start gap-3">
                      <ModelIconImg
                        model={model.value}
                        provider={model.provider}
                        size={34}
                      />
                      <div className="min-w-0">
                        <h3 className="truncate text-sm font-semibold text-[var(--theme-text)]">
                          {model.label}
                        </h3>
                        <p className="mt-1 truncate text-xs text-stone-500 dark:text-stone-400">
                          {model.value}
                        </p>
                      </div>
                    </div>
                    {isDefault ? (
                      <span className="shrink-0 rounded-md bg-emerald-50 px-2 py-1 text-[11px] font-semibold text-emerald-700 dark:bg-emerald-900/25 dark:text-emerald-200">
                        {t("models.defaultModel", "默认")}
                      </span>
                    ) : null}
                  </div>

                  {model.description ? (
                    <p className="mt-3 line-clamp-2 text-xs leading-5 text-stone-500 dark:text-stone-400">
                      {model.description}
                    </p>
                  ) : null}

                  <dl className="mt-4 grid grid-cols-2 gap-2 text-xs">
                    <div className="rounded-md bg-[var(--theme-bg-sidebar)] p-2">
                      <dt className="text-stone-400 dark:text-stone-500">
                        {t("models.provider", "供应商")}
                      </dt>
                      <dd className="mt-1 truncate font-medium text-[var(--theme-text)]">
                        {providerLabel(model)}
                      </dd>
                    </div>
                    <div className="rounded-md bg-[var(--theme-bg-sidebar)] p-2">
                      <dt className="text-stone-400 dark:text-stone-500">
                        {t("models.contextWindow", "上下文")}
                      </dt>
                      <dd className="mt-1 truncate font-medium text-[var(--theme-text)]">
                        {contextWindowLabel(
                          model,
                          t("models.contextWindowUnknown", "Not published"),
                        )}
                      </dd>
                    </div>
                  </dl>
                </article>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
