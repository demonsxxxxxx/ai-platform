import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  Boxes,
  FolderOpen,
  Search,
  Server,
  ShieldCheck,
  Wrench,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { PanelHeader } from "../common/PanelHeader";
import { MCPPanelSkeleton } from "../skeletons";
import { Pagination } from "../common/Pagination";
import { GovernanceAvailabilityBadge } from "../governance/GovernanceAvailabilityBadge";
import { resolveGroupAvailability } from "../governance/groupAvailability";
import { useMCP } from "../../hooks/useMcp";
import { useAuth } from "../../hooks/useAuth";
import { Permission } from "../../types";
import type { MCPServerResponse } from "../../types";

function roleQuotaCount(server: MCPServerResponse): number {
  return Object.values(server.role_quotas ?? {}).filter(Boolean).length;
}

function transportLabel(transport: MCPServerResponse["transport"]): string {
  if (transport === "streamable_http") return "HTTP";
  if (transport === "sandbox") return "Sandbox";
  return "SSE";
}

export function MCPPanel() {
  const { t } = useTranslation();
  const [searchQuery, setSearchQuery] = useState("");
  const [page, setPage] = useState(1);
  const pageSize = 20;
  const listParams = useMemo(
    () => ({
      skip: (page - 1) * pageSize,
      limit: pageSize,
      q: searchQuery.trim() || undefined,
    }),
    [page, searchQuery],
  );
  const { servers, total, isLoading, error } = useMCP({ listParams });
  const { hasAnyPermission } = useAuth();

  useEffect(() => {
    setPage(1);
  }, [searchQuery]);

  const canRead = hasAnyPermission([Permission.MCP_READ]);
  const canSelect = hasAnyPermission([Permission.MCP_READ]);
  const permissionAvailability = resolveGroupAvailability({
    enabled: canSelect,
    inherited: canRead && !canSelect,
  });
  const lifecycleAvailability = resolveGroupAvailability({ backed: false });

  if (!canRead) {
    return (
      <div
        data-phase1c-surface="mcp"
        className="flex h-full min-h-0 items-center justify-center p-6"
      >
        <section className="max-w-xl rounded-lg border border-stone-200 bg-white p-5 text-center shadow-[0_4px_12px_rgba(18,38,63,0.03)] dark:border-stone-800 dark:bg-stone-900">
          <ShieldCheck className="mx-auto text-stone-500" size={32} />
          <h2 className="mt-4 text-base font-semibold text-stone-900 dark:text-stone-100">
            {t("mcp.permissionLimited.title")}
          </h2>
          <p className="mt-2 text-sm leading-6 text-stone-600 dark:text-stone-300">
            {t("mcp.permissionLimited.description")}
          </p>
          <div className="mt-4 flex justify-center">
            <GovernanceAvailabilityBadge
              state={permissionAvailability.state}
              labelKey={permissionAvailability.labelKey}
            />
          </div>
        </section>
      </div>
    );
  }

  if (isLoading) {
    return <MCPPanelSkeleton />;
  }

  return (
    <div data-phase1c-surface="mcp" className="glass-shell flex h-full min-h-0 flex-col">
      <PanelHeader
        title={t("mcp.title")}
        subtitle={t("mcp.subtitle")}
        icon={<Server size={20} className="text-theme-text-secondary" />}
        searchValue={searchQuery}
        onSearchChange={setSearchQuery}
        searchPlaceholder={t("mcp.searchPlaceholder")}
      />

      {error && (
        <div className="mx-4 mt-4 flex items-start gap-2 rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-900/30 dark:text-red-300">
          <AlertCircle size={16} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <div className="px-4 pb-2 pt-3">
        <section className="rounded-lg border border-stone-200/70 bg-white p-3 shadow-[0_4px_12px_rgba(18,38,63,0.03)] dark:border-stone-800 dark:bg-stone-900">
          <div className="grid gap-3 lg:grid-cols-3">
            <div className="flex items-start justify-between gap-3 rounded-md bg-stone-50/80 p-3 dark:bg-stone-950/40">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <ShieldCheck size={16} className="text-stone-500" />
                  <h3 className="text-sm font-semibold text-stone-900 dark:text-stone-100">
                    {t("mcp.permissionLimited.title")}
                  </h3>
                </div>
                <p className="mt-1 text-xs leading-5 text-stone-500 dark:text-stone-400">
                  {t("mcp.permissionLimited.description")}
                </p>
              </div>
              <GovernanceAvailabilityBadge
                state={permissionAvailability.state}
                labelKey={permissionAvailability.labelKey}
              />
            </div>
            <div className="flex items-start justify-between gap-3 rounded-md bg-stone-50/80 p-3 dark:bg-stone-950/40">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <ShieldCheck size={16} className="text-stone-500" />
                  <h3 className="text-sm font-semibold text-stone-900 dark:text-stone-100">
                    {t("mcp.permissionMode")}
                  </h3>
                </div>
                <p className="mt-1 text-xs leading-5 text-stone-500 dark:text-stone-400">
                  {t("mcp.addToComposer")}
                </p>
              </div>
              <GovernanceAvailabilityBadge
                state={permissionAvailability.state}
                labelKey={permissionAvailability.labelKey}
              />
            </div>
            <div className="flex items-start justify-between gap-3 rounded-md bg-stone-50/80 p-3 dark:bg-stone-950/40">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <Boxes size={16} className="text-stone-500" />
                  <h3 className="text-sm font-semibold text-stone-900 dark:text-stone-100">
                    {t("mcp.lifecycleUnavailable")}
                  </h3>
                </div>
                <p className="mt-1 text-xs leading-5 text-stone-500 dark:text-stone-400">
                  {t("mcp.lifecycleUnavailableDescription")}
                </p>
              </div>
              <GovernanceAvailabilityBadge
                state={lifecycleAvailability.state}
                labelKey={lifecycleAvailability.labelKey}
              />
            </div>
          </div>
        </section>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-3">
        {servers.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center text-theme-text-secondary">
            {searchQuery ? (
              <Search size={42} className="mb-3 text-theme-text-secondary" />
            ) : (
              <FolderOpen size={42} className="mb-3 text-theme-text-secondary" />
            )}
            <p className="text-center text-sm">
              {searchQuery ? t("mcp.noMatchingServers") : t("mcp.noServers")}
            </p>
            <p className="mt-2 max-w-md text-center text-xs leading-5 text-stone-500 dark:text-stone-400">
              {t("mcp.lifecycleUnavailableDescription")}
            </p>
          </div>
        ) : (
          <div className="grid gap-3 lg:grid-cols-2">
            {servers.map((server) => {
              const availability = resolveGroupAvailability({
                enabled: server.enabled,
              });
              const roleCount = server.allowed_roles?.length ?? 0;
              const quotaCount = roleQuotaCount(server);
              return (
                <article
                  key={server.name}
                  className="rounded-lg border border-stone-200 bg-white p-4 shadow-[0_4px_12px_rgba(18,38,63,0.03)] dark:border-stone-800 dark:bg-stone-900"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <Wrench size={16} className="shrink-0 text-stone-500" />
                        <h3 className="truncate text-sm font-semibold text-stone-900 dark:text-stone-100">
                          {server.name}
                        </h3>
                      </div>
                      <p className="mt-1 text-xs leading-5 text-stone-500 dark:text-stone-400">
                        {server.is_system
                          ? t("mcp.card.system")
                          : t("mcp.card.user")}
                        {" · "}
                        {transportLabel(server.transport)}
                      </p>
                    </div>
                    <GovernanceAvailabilityBadge
                      state={availability.state}
                      labelKey={availability.labelKey}
                    />
                  </div>

                  <dl className="mt-4 grid grid-cols-3 gap-2 text-xs">
                    <div className="rounded-md bg-stone-50 p-2 dark:bg-stone-950/50">
                      <dt className="text-stone-400 dark:text-stone-500">
                        {t("mcp.permissionMode")}
                      </dt>
                      <dd className="mt-1 font-medium text-stone-700 dark:text-stone-200">
                        {server.enabled
                          ? t("governance.enabled")
                          : t("governance.disabled")}
                      </dd>
                    </div>
                    <div className="rounded-md bg-stone-50 p-2 dark:bg-stone-950/50">
                      <dt className="text-stone-400 dark:text-stone-500">
                        {t("mcp.card.roleCount", { count: roleCount })}
                      </dt>
                      <dd className="mt-1 font-medium text-stone-700 dark:text-stone-200">
                        {roleCount || t("mcp.form.allRoles")}
                      </dd>
                    </div>
                    <div className="rounded-md bg-stone-50 p-2 dark:bg-stone-950/50">
                      <dt className="text-stone-400 dark:text-stone-500">
                        {t("mcp.card.roleQuotaCount")}
                      </dt>
                      <dd className="mt-1 font-medium text-stone-700 dark:text-stone-200">
                        {quotaCount}
                      </dd>
                    </div>
                  </dl>

                  <div className="mt-3 rounded-md border border-dashed border-stone-200 p-2 text-xs leading-5 text-stone-500 dark:border-stone-800 dark:text-stone-400">
                    {t("mcp.lifecycleUnavailableDescription")}
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </div>

      {total > pageSize && (
        <div className="glass-divider px-3 py-3 sm:px-4">
          <Pagination
            page={page}
            pageSize={pageSize}
            total={total}
            onChange={setPage}
          />
        </div>
      )}
    </div>
  );
}
