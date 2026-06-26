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
import { isPermissionError } from "../governance/frontendGovernanceState";
import { resolveGroupAvailability } from "../governance/groupAvailability";
import { WorkbenchStateSurface } from "../workbench/WorkbenchStateSurface";
import { workbenchSurface } from "../workbench/workbenchSurface";
import { useAuth } from "../../hooks/useAuth";
import { useMCP } from "../../hooks/useMcp";
import { Permission } from "../../types";
import type { MCPServerResponse } from "../../types";
import { resolveMcpGovernanceState } from "./mcpGovernanceState";

const MCP_ADMIN_ROLE_ALIASES = new Set([
  "admin",
  "developer",
  "platform_admin",
  "break_glass_admin",
]);

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
  const {
    user,
    hasPermission,
    hasAnyPermission,
    isAuthenticated,
    isLoading: authLoading,
  } = useAuth();
  const [searchQuery, setSearchQuery] = useState("");
  const [page, setPage] = useState(1);
  const [permissionDenied, setPermissionDenied] = useState(false);
  const canReadMcp = hasPermission(Permission.MCP_READ);
  const pageSize = 20;
  const listParams = useMemo(
    () => ({
      skip: (page - 1) * pageSize,
      limit: pageSize,
      q: searchQuery.trim() || undefined,
    }),
    [page, searchQuery],
  );
  useEffect(() => {
    setPage(1);
  }, [searchQuery]);

  const { servers, total, isLoading, error } = useMCP({
    enabled: !permissionDenied,
    listParams,
  });

  useEffect(() => {
    if (isPermissionError(error)) {
      setPermissionDenied(true);
    }
  }, [error]);
  const canManageMcp =
    hasAnyPermission([
      Permission.MCP_ADMIN,
      Permission.MCP_WRITE_SSE,
      Permission.MCP_WRITE_HTTP,
      Permission.MCP_WRITE_SANDBOX,
      Permission.MCP_DELETE,
    ]) ||
    Boolean(
      user?.roles?.some((role) =>
        MCP_ADMIN_ROLE_ALIASES.has(role.trim().toLowerCase()),
      ),
    ) ||
    servers.some((server) => server.can_edit);
  const mcpGovernance = resolveMcpGovernanceState({
    isAuthenticated,
    isLoading: authLoading || isLoading,
    canReadMcp,
    canManageMcp,
    servers,
    total,
    loadError: error,
  });
  const permissionAvailability = resolveGroupAvailability({
    backed: !mcpGovernance.governedUnavailable,
    enabled: !mcpGovernance.governedUnavailable,
  });
  const lifecycleAvailability = mcpGovernance.lifecycleAvailability;
  const credentialsAvailability = mcpGovernance.credentialsAvailability;

  if (mcpGovernance.pageState === "loading") {
    return (
      <div
        data-phase1c-surface="mcp"
        data-mcp-directory-shell
        data-frontend-governance-state={mcpGovernance.pageState}
        data-required-permission={mcpGovernance.requiredPermission}
        data-auth-projection-has-permission={
          mcpGovernance.authProjectionHasPermission
        }
        className={workbenchSurface.statePage}
      >
        <MCPPanelSkeleton />
      </div>
    );
  }

  if (
    mcpGovernance.pageState === "logged-out" ||
    mcpGovernance.pageState === "no-workspace" ||
    mcpGovernance.pageState === "forbidden"
  ) {
    return (
      <div
        data-phase1c-surface="mcp"
        data-mcp-directory-shell
        data-frontend-governance-state={mcpGovernance.pageState}
        data-required-permission={mcpGovernance.requiredPermission}
        data-auth-projection-has-permission={
          mcpGovernance.authProjectionHasPermission
        }
        className={workbenchSurface.statePage}
      >
        <WorkbenchStateSurface
          state={mcpGovernance.pageState}
          surface="mcp-directory"
          title={
            mcpGovernance.pageState === "forbidden"
              ? t("mcp.noPermission")
              : undefined
          }
          description={
            mcpGovernance.pageState === "forbidden"
              ? t("mcp.catalogUnavailable.description")
              : undefined
          }
          details={[error].filter((item): item is string => Boolean(item))}
          capabilities={[
            {
              title: t("mcp.permissionLimited.title"),
              description: t("mcp.permissionLimited.description"),
              state: mcpGovernance.directoryAvailability.state,
              labelKey: mcpGovernance.directoryAvailability.labelKey,
            },
            {
              title: t("mcp.lifecycleGovernance.title"),
              description: t("mcp.lifecycleGovernance.description"),
              state: lifecycleAvailability.state,
              labelKey: lifecycleAvailability.labelKey,
            },
          ]}
        />
      </div>
    );
  }

  return (
    <div
      data-phase1c-surface="mcp"
      data-mcp-directory-shell
      data-frontend-governance-state={mcpGovernance.pageState}
      data-required-permission={mcpGovernance.requiredPermission}
      data-auth-projection-has-permission={
        mcpGovernance.authProjectionHasPermission
      }
      className={workbenchSurface.page}
    >
      <PanelHeader
        title={t("mcp.title")}
        subtitle={t("mcp.subtitle")}
        icon={<Server size={20} className="text-theme-text-secondary" />}
        searchValue={searchQuery}
        onSearchChange={setSearchQuery}
        searchPlaceholder={t("mcp.searchPlaceholder")}
      />

      {error && (
        <div className="mx-4 mt-4 flex items-start gap-2 rounded-lg bg-[var(--theme-danger-soft)] p-3 text-sm text-[var(--theme-danger)] ring-1 ring-[var(--theme-danger-ring)]">
          <AlertCircle size={16} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <div className={workbenchSurface.catalog.summaryGridFour}>
        <section className={`${workbenchSurface.catalog.summaryCard} flex items-start justify-between gap-3`}>
          <div className="flex min-w-0 items-start gap-3">
            <div className={workbenchSurface.catalog.compactIconBox}>
              <ShieldCheck size={16} />
            </div>
            <div className="min-w-0">
              <h3 className={workbenchSurface.catalog.title}>
                {t("mcp.permissionLimited.title")}
              </h3>
              <p className={`mt-1 ${workbenchSurface.catalog.body}`}>
                {t("mcp.permissionLimited.description")}
              </p>
            </div>
          </div>
          <GovernanceAvailabilityBadge
            state={mcpGovernance.directoryAvailability.state}
            labelKey={mcpGovernance.directoryAvailability.labelKey}
          />
        </section>
        <section className={`${workbenchSurface.catalog.summaryCard} flex items-start justify-between gap-3`}>
          <div className="flex min-w-0 items-start gap-3">
            <div className={workbenchSurface.catalog.compactIconBox}>
              <ShieldCheck size={16} />
            </div>
            <div className="min-w-0">
              <h3 className={workbenchSurface.catalog.title}>
                {t("mcp.permissionMode")}
              </h3>
              <p className={`mt-1 ${workbenchSurface.catalog.body}`}>
                {t("mcp.addToComposer")}
              </p>
            </div>
          </div>
          <GovernanceAvailabilityBadge
            state={permissionAvailability.state}
            labelKey={permissionAvailability.labelKey}
          />
        </section>
        <section
          data-fail-closed-surface="mcp-lifecycle"
          className={`${workbenchSurface.catalog.summaryCard} flex items-start justify-between gap-3`}
        >
          <div className="flex min-w-0 items-start gap-3">
            <div className={workbenchSurface.catalog.compactIconBox}>
              <Boxes size={16} />
            </div>
            <div className="min-w-0">
              <h3 className={workbenchSurface.catalog.title}>
                {t("mcp.lifecycleGovernance.title")}
              </h3>
              <p className={`mt-1 ${workbenchSurface.catalog.body}`}>
                {t("mcp.lifecycleGovernance.description")}
              </p>
              <p className={`mt-1 ${workbenchSurface.catalog.body}`}>
                {t("mcp.credentialsGovernance.description")}
              </p>
            </div>
          </div>
          <GovernanceAvailabilityBadge
            state={lifecycleAvailability.state}
            labelKey={lifecycleAvailability.labelKey}
          />
        </section>
        <section
          data-fail-closed-surface="mcp-credentials"
          className={`${workbenchSurface.catalog.summaryCard} flex items-start justify-between gap-3`}
        >
          <div className="flex min-w-0 items-start gap-3">
            <div className={workbenchSurface.catalog.compactIconBox}>
              <ShieldCheck size={16} />
            </div>
            <div className="min-w-0">
              <h3 className={workbenchSurface.catalog.title}>
                {t("mcp.credentialsGovernance.title")}
              </h3>
              <p className={`mt-1 ${workbenchSurface.catalog.body}`}>
                {t("mcp.credentialsGovernance.description")}
              </p>
            </div>
          </div>
          <GovernanceAvailabilityBadge
            state={credentialsAvailability.state}
            labelKey={credentialsAvailability.labelKey}
          />
        </section>
      </div>
      {mcpGovernance.pageState === "degraded" ? (
        <div className="px-4 pb-2 pt-1">
          <WorkbenchStateSurface
            state="degraded"
            surface="mcp-directory"
            title={t("mcp.catalogUnavailable.title")}
            description={t("mcp.catalogUnavailable.description")}
            details={[error].filter((item): item is string => Boolean(item))}
            className="max-w-none text-left"
          />
        </div>
      ) : null}

      <div className={workbenchSurface.catalog.content}>
        {servers.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center text-theme-text-secondary">
            {searchQuery ? (
              <Search size={42} className="mb-3 text-theme-text-secondary" />
            ) : (
              <FolderOpen size={42} className="mb-3 text-theme-text-secondary" />
            )}
            <p className="text-center text-sm">
              {mcpGovernance.governedUnavailable
                ? t("mcp.catalogUnavailable.title")
                : searchQuery
                ? t("mcp.noMatchingServers")
                : t("mcp.noServers")}
            </p>
            <p className="mt-2 max-w-md text-center text-xs leading-5 text-[var(--theme-text-secondary)]">
              {mcpGovernance.governedUnavailable
                ? t("mcp.catalogUnavailable.description")
                : t("mcp.lifecycleGovernance.description")}
            </p>
          </div>
        ) : (
          <div className={workbenchSurface.catalog.cardGrid}>
            {servers.map((server) => {
              const availability = resolveGroupAvailability({
                enabled: server.enabled,
              });
              const roleCount = server.allowed_roles?.length ?? 0;
              const quotaCount = roleQuotaCount(server);
              return (
                <article
                  key={server.name}
                  className={workbenchSurface.catalog.entryCard}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <Wrench size={16} className="shrink-0 text-[var(--theme-text-secondary)]" />
                        <h3 className={`truncate ${workbenchSurface.catalog.title}`}>
                          {server.name}
                        </h3>
                      </div>
                      <p className={`mt-1 ${workbenchSurface.catalog.body}`}>
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
                    <div className={workbenchSurface.catalog.metricTile}>
                      <dt className={workbenchSurface.catalog.label}>
                        {t("mcp.permissionMode")}
                      </dt>
                      <dd className="mt-1 font-medium text-[var(--theme-text)]">
                        {server.enabled
                          ? t("governance.enabled")
                          : t("governance.disabled")}
                      </dd>
                    </div>
                    <div className={workbenchSurface.catalog.metricTile}>
                      <dt className={workbenchSurface.catalog.label}>
                        {t("mcp.card.roleCount", { count: roleCount })}
                      </dt>
                      <dd className="mt-1 font-medium text-[var(--theme-text)]">
                        {roleCount || t("mcp.form.allRoles")}
                      </dd>
                    </div>
                    <div className={workbenchSurface.catalog.metricTile}>
                      <dt className={workbenchSurface.catalog.label}>
                        {t("mcp.card.roleQuotaCount")}
                      </dt>
                      <dd className="mt-1 font-medium text-[var(--theme-text)]">
                        {quotaCount}
                      </dd>
                    </div>
                  </dl>

                  <div className="mt-3 rounded-md border border-dashed border-[var(--theme-border)] p-2 text-xs leading-5 text-[var(--theme-text-secondary)]">
                    {t("mcp.lifecycleGovernance.description")}
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </div>

      {total > pageSize && (
        <div className="enterprise-divider border-t px-3 py-3 sm:px-4">
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
