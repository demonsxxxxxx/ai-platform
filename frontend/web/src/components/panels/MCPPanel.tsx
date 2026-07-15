import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  Boxes,
  Edit3,
  FolderOpen,
  Plus,
  Search,
  Server,
  ShieldCheck,
  ToggleLeft,
  ToggleRight,
  Trash2,
  X,
  Wrench,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import toast from "react-hot-toast";
import { PanelHeader } from "../common/PanelHeader";
import { MCPPanelSkeleton } from "../skeletons";
import { Pagination } from "../common/Pagination";
import { ConfirmDialog } from "../common/ConfirmDialog";
import { GovernanceAvailabilityBadge } from "../governance/GovernanceAvailabilityBadge";
import {
  buildFrontendGovernanceSmokeAttributes,
  isPermissionError,
} from "../governance/frontendGovernanceState";
import { WorkbenchStateSurface } from "../workbench/WorkbenchStateSurface";
import { workbenchSurface } from "../workbench/workbenchSurface";
import { useAuth } from "../../hooks/useAuth";
import { useMCP } from "../../hooks/useMcp";
import { Permission } from "../../types";
import type { MCPServerCreate, MCPServerResponse } from "../../types";
import { MCPServerForm } from "../mcp/MCPServerForm";
import { canManageMcpLifecycle, isAiAdminUser } from "./capabilityAdmin";
import { resolveMcpGovernanceState } from "./mcpGovernanceState";
import { OrdinaryMcpCatalog } from "./OrdinaryMcpCatalog";

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
  const [editorServer, setEditorServer] = useState<MCPServerResponse | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<MCPServerResponse | null>(null);
  const editorDialogRef = useRef<HTMLElement>(null);
  const editorPreviousFocusRef = useRef<HTMLElement | null>(null);
  const editorLoadingRef = useRef(false);
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

  const {
    servers,
    total,
    isLoading,
    error,
    createServer,
    updateServer,
    deleteServer,
    toggleServer,
  } = useMCP({
    enabled: !permissionDenied,
    listParams,
  });
  editorLoadingRef.current = isLoading;

  useEffect(() => {
    if (isPermissionError(error)) {
      setPermissionDenied(true);
    }
  }, [error]);
  const isAiAdmin = isAiAdminUser(user);
  const canManageMcp = canManageMcpLifecycle({
    hasExplicitMcpPermission: hasAnyPermission([
      Permission.MCP_ADMIN,
      Permission.MCP_WRITE_SSE,
      Permission.MCP_WRITE_HTTP,
      Permission.MCP_WRITE_SANDBOX,
      Permission.MCP_DELETE,
    ]),
    isAiAdmin,
  });
  const directoryIsLoading =
    authLoading || (isLoading && servers.length === 0 && !editorOpen);
  const mcpGovernance = resolveMcpGovernanceState({
    isAuthenticated,
    isLoading: directoryIsLoading,
    canReadMcp,
    canManageMcp,
    servers,
    total,
    loadError: error,
  });
  const lifecycleAvailability = mcpGovernance.lifecycleAvailability;
  const canManageMcpUi = canManageMcp && !mcpGovernance.governedUnavailable;

  const openEditor = (server: MCPServerResponse | null) => {
    if (!canManageMcpUi) return;
    editorPreviousFocusRef.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    setEditorServer(server);
    setEditorOpen(true);
  };

  const openCreate = () => openEditor(null);

  const closeEditor = () => {
    if (!editorLoadingRef.current) setEditorOpen(false);
  };

  useEffect(() => {
    if (canManageMcpUi) return;
    setEditorOpen(false);
    setEditorServer(null);
    setDeleteTarget(null);
  }, [canManageMcpUi]);

  useEffect(() => {
    if (!editorOpen || !canManageMcpUi) return undefined;

    const dialog = editorDialogRef.current;
    if (!dialog) return undefined;

    const focusableSelector = [
      "button:not([disabled])",
      "input:not([disabled])",
      "select:not([disabled])",
      "textarea:not([disabled])",
      '[tabindex]:not([tabindex="-1"])',
    ].join(",");
    const initialFocus = dialog.querySelector<HTMLElement>(
      "[data-mcp-editor-initial-focus]",
    );
    initialFocus?.focus();

    const handleEditorKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (!editorLoadingRef.current) {
          event.preventDefault();
          setEditorOpen(false);
        }
        return;
      }
      if (event.key !== "Tab") return;

      const focusable = Array.from(
        dialog.querySelectorAll<HTMLElement>(focusableSelector),
      ).filter((element) => element.getAttribute("aria-hidden") !== "true");
      if (focusable.length === 0) {
        event.preventDefault();
        dialog.focus();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const activeElement = document.activeElement;
      if (
        event.shiftKey &&
        (activeElement === first || !dialog.contains(activeElement))
      ) {
        event.preventDefault();
        last.focus();
      } else if (
        !event.shiftKey &&
        (activeElement === last || !dialog.contains(activeElement))
      ) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", handleEditorKeyDown);
    return () => {
      document.removeEventListener("keydown", handleEditorKeyDown);
      editorPreviousFocusRef.current?.focus();
      editorPreviousFocusRef.current = null;
    };
  }, [editorOpen, canManageMcpUi]);

  const saveServer = async (data: MCPServerCreate): Promise<boolean> => {
    const saved = editorServer
      ? await updateServer(editorServer.name, data, editorServer.is_system)
      : await createServer(data, true);
    if (saved) {
      setEditorOpen(false);
      toast.success(t(editorServer ? "mcp.admin.updateSuccess" : "mcp.admin.createSuccess"));
    }
    return Boolean(saved);
  };

  const confirmDelete = async () => {
    if (!canManageMcpUi || !deleteTarget) return;
    const deleted = await deleteServer(deleteTarget.name, deleteTarget.is_system);
    if (deleted) {
      setDeleteTarget(null);
      toast.success(t("mcp.admin.deleteSuccess"));
    }
  };

  if (!isAiAdmin) {
    return (
      <OrdinaryMcpCatalog
        servers={servers}
        isLoading={directoryIsLoading}
        listError={error}
      />
    );
  }

  if (mcpGovernance.pageState === "loading") {
    return (
      <div
        data-phase1c-surface="mcp"
        data-mcp-directory-shell
        {...buildFrontendGovernanceSmokeAttributes(mcpGovernance.pageState)}
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
        {...buildFrontendGovernanceSmokeAttributes(mcpGovernance.pageState)}
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
      {...buildFrontendGovernanceSmokeAttributes(mcpGovernance.pageState)}
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
      {canManageMcpUi ? (
        <div className="flex justify-end px-4 pt-3" data-mcp-admin-controls>
          <button
            type="button"
            onClick={openCreate}
            className="btn-primary inline-flex min-h-11 items-center justify-center gap-2"
            aria-label={t("mcp.admin.addServer")}
            title={t("mcp.admin.addServer")}
          >
            <Plus size={18} />
            <span className="hidden sm:inline">{t("mcp.admin.addServer")}</span>
          </button>
        </div>
      ) : null}

      {error && (
        <div className="mx-4 mt-4 flex items-start gap-2 rounded-lg bg-[var(--theme-danger-soft)] p-3 text-sm text-[var(--theme-danger)] ring-1 ring-[var(--theme-danger-ring)]">
          <AlertCircle size={16} className="mt-0.5 shrink-0" />
          <span>{t("mcp.admin.operationFailed")}</span>
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
                {t("mcp.admin.directorySummary")}
              </p>
            </div>
          </div>
          <span data-mcp-summary-status>
            <GovernanceAvailabilityBadge
              state={mcpGovernance.directoryAvailability.state}
              labelKey={mcpGovernance.directoryAvailability.labelKey}
            />
          </span>
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
                {t("mcp.admin.permissionSummary")}
              </p>
            </div>
          </div>
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
                {t("mcp.admin.lifecycleSummary")}
              </p>
              <p className={`mt-1 ${workbenchSurface.catalog.body}`}>
                {t("mcp.admin.credentialsSummary")}
              </p>
            </div>
          </div>
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
        </section>
      </div>
      {mcpGovernance.pageState === "degraded" ? (
        <div className="px-4 pb-2 pt-1">
          <WorkbenchStateSurface
            state="degraded"
            surface="mcp-directory"
            title={t("mcp.catalogUnavailable.title")}
            description={t("mcp.catalogUnavailable.description")}
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
            {canManageMcpUi && !searchQuery ? (
              <button
                type="button"
                onClick={openCreate}
                className="btn-primary mt-4 inline-flex min-h-11 items-center gap-2"
              >
                <Plus size={18} />
                {t("mcp.admin.addFirstServer")}
              </button>
            ) : null}
          </div>
        ) : (
          <div className={workbenchSurface.catalog.cardGrid}>
            {servers.map((server) => {
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
                      </div>

                      {canManageMcpUi ? (
                        <div className="mt-3 flex items-center justify-end gap-1" data-mcp-admin-controls>
                          <button type="button" onClick={async () => { const updated = await toggleServer(server.name); if (updated) toast.success(t(updated.enabled ? "mcp.admin.enableSuccess" : "mcp.admin.disableSuccess")); }} className="btn-icon min-h-11 min-w-11" aria-label={server.enabled ? t("mcp.admin.disableServer") : t("mcp.admin.enableServer")} title={server.enabled ? t("mcp.admin.disableServer") : t("mcp.admin.enableServer")} disabled={isLoading}>
                            {server.enabled ? <ToggleRight size={18} /> : <ToggleLeft size={18} />}
                          </button>
                          <button type="button" onClick={() => openEditor(server)} className="btn-icon min-h-11 min-w-11" aria-label={t("mcp.admin.editServer")} title={t("mcp.admin.editServer")} disabled={isLoading}>
                            <Edit3 size={18} />
                          </button>
                          <button type="button" onClick={() => setDeleteTarget(server)} className="btn-icon min-h-11 min-w-11 hover:bg-[var(--theme-danger-soft)] hover:text-[var(--theme-danger)]" aria-label={t("mcp.admin.deleteServer")} title={t("mcp.admin.deleteServer")} disabled={isLoading}>
                            <Trash2 size={18} />
                          </button>
                        </div>
                      ) : null}

                  <dl className="mt-4 grid grid-cols-3 gap-2 text-xs">
                    <div className={workbenchSurface.catalog.metricTile}>
                      <dt className={workbenchSurface.catalog.label}>
                        {t("mcp.card.statusLabel")}
                      </dt>
                      <dd className="mt-1 font-medium text-[var(--theme-text)]">
                        {server.enabled
                          ? t("mcp.card.statusEnabled")
                          : t("mcp.card.statusDisabled")}
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
      {editorOpen && canManageMcpUi ? (
        <div className="fixed inset-0 z-[300] flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label={editorServer ? t("mcp.admin.editServer") : t("mcp.admin.addServer")}>
          <div className="absolute inset-0 bg-[var(--theme-overlay-strong)]" onClick={closeEditor} />
          <section ref={editorDialogRef} tabIndex={-1} className="enterprise-modal-shell relative z-10 max-h-[calc(100dvh-2rem)] w-full max-w-2xl overflow-y-auto">
            <div className="enterprise-modal-footer justify-between">
              <h2 className="text-base font-semibold text-[var(--theme-text)]">{editorServer ? t("mcp.admin.editServer") : t("mcp.admin.addServer")}</h2>
              <button type="button" data-mcp-editor-initial-focus className="btn-icon" onClick={closeEditor} aria-label={t("common.close")} disabled={isLoading}><X size={18} /></button>
            </div>
            <div className="p-5"><MCPServerForm server={editorServer} onSave={saveServer} onCancel={closeEditor} isLoading={isLoading} isSystemServer /></div>
          </section>
        </div>
      ) : null}
      <ConfirmDialog
        isOpen={canManageMcpUi && Boolean(deleteTarget)}
        title={t("mcp.admin.deleteServer")}
        message={t("mcp.admin.deleteConfirmation", { name: deleteTarget?.name ?? "" })}
        confirmText={t("mcp.admin.deleteServer")}
        onConfirm={confirmDelete}
        onCancel={() => setDeleteTarget(null)}
        loading={isLoading}
      />
    </div>
  );
}
